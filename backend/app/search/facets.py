"""Bounded facet counts for one investigation; every count is matching root articles."""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryCluster
from app.feeds.models import Article, Feed
from app.nlp.models import Entity, Keyword
from app.search.aggregations import Counted, ensure_complete, read_terms, terms
from app.search.elasticsearch import ElasticsearchAdapter
from app.search.schemas import FacetBucket, FacetGroup, SearchFacets

MAX_FACET_BUCKETS = 25
# Response field -> (nested path, or None for an article-level field; indexed field).
GROUPS: dict[str, tuple[str | None, str]] = {
    "sources": ("provenance", "provenance.source_id"),
    "source_countries": ("provenance", "provenance.source_country"),
    "story_countries": (None, "primary_story_country"),
    "mentioned_countries": (None, "mentioned_countries"),
    "languages": (None, "detected_language"),
    "entities": ("entities", "entities.id"),
    "entity_types": ("entities", "entities.type"),
    "keywords": (None, "keyword_ids"),
    "story_clusters": (None, "story_cluster_id"),
}


def facets_body(query: dict[str, Any], limit: int) -> dict[str, Any]:
    size = min(limit, MAX_FACET_BUCKETS)
    aggs = {name: terms(path, field, size) for name, (path, field) in GROUPS.items()}
    return {"size": 0, "track_total_hits": True, "query": query, "aggs": aggs}


def parse_facets(response: dict[str, Any]) -> tuple[int, dict[str, Counted]]:
    ensure_complete(response)
    aggregations = response.get("aggregations", {})
    groups = {
        name: read_terms(aggregations.get(name, {}), nested=path is not None)
        for name, (path, _field) in GROUPS.items()
    }
    return int(response["hits"]["total"]["value"]), groups


def _ids(counted: Counted) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    for value, _count in counted.buckets:
        try:
            found.append(uuid.UUID(value))
        except ValueError:
            continue  # not a catalogue id, so it cannot be labelled
    return found


def _label_queries(groups: dict[str, Counted]) -> dict[str, Select[Any]]:
    return {
        "sources": select(Feed.id, Feed.name).where(Feed.id.in_(_ids(groups["sources"]))),
        "entities": select(Entity.id, Entity.display_text).where(
            Entity.id.in_(_ids(groups["entities"]))
        ),
        "keywords": select(Keyword.id, Keyword.display_text).where(
            Keyword.id.in_(_ids(groups["keywords"]))
        ),
        "story_clusters": select(StoryCluster.id, Article.title)
        .outerjoin(Article, Article.id == StoryCluster.representative_article_id)
        .where(StoryCluster.id.in_(_ids(groups["story_clusters"]))),
    }


async def search_facets(
    db: AsyncSession,
    adapter: ElasticsearchAdapter,
    index_name: str,
    query: dict[str, Any],
    limit: int,
) -> SearchFacets:
    total, groups = parse_facets(await adapter.search_index(index_name, facets_body(query, limit)))
    # One batched query per catalogue; a bucket the catalogue no longer holds is dropped.
    # ponytail: dropping happens after the ES top-N cut, so a group can return fewer than `limit`
    # buckets while ranks past the cut stay hidden; request limit + slack and trim here if it bites.
    labels = {
        name: {str(key): label for key, label in (await db.execute(statement)).all()}
        for name, statement in _label_queries(groups).items()
    }
    output: dict[str, FacetGroup] = {}
    for name, counted in groups.items():
        known = labels.get(name)
        output[name] = FacetGroup(
            buckets=[
                FacetBucket(
                    value=value, label=known[value] if known is not None else None, count=count
                )
                for value, count in counted.buckets
                if known is None or value in known
            ],
            truncated=counted.truncated,
        )
    return SearchFacets(total=total, **output)
