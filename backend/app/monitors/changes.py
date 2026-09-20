import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryCluster, StoryClusterMember
from app.core.config import get_settings
from app.feeds.models import Article, Feed, FeedArticle
from app.monitors.evaluation import criteria_params, window_filter
from app.monitors.models import Monitor
from app.monitors.schemas import (
    ChangeEvidence,
    EntityChange,
    MonitorChanges,
    SourceChange,
    StoryChange,
)
from app.monitors.service import parse_state
from app.nlp.models import Entity
from app.search.criteria import build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

TOP = 10
EVIDENCE = 3
# A story "grew" when this many distinct sources joined it inside the window.
MATERIAL_SOURCE_GROWTH = 2
# Index schema that first carries each kind: entities in v2, story clusters in v3.
KINDS = {"sources": 1, "entities": 2, "stories": 3}
_NESTED = {
    "sources": ("provenance", "provenance.source_id"),
    "entities": ("entities", "entities.id"),
}
_SORT = [{"first_discovered_at": "desc"}, {"article_id": "desc"}]


@dataclass(frozen=True)
class Found:
    """One aggregation bucket: a key that occurs in the window, how often, and sample articles."""

    key: str
    count: int
    evidence: list[ChangeEvidence]


@dataclass(frozen=True)
class Labels:
    feeds: dict[str, str]
    entities: dict[str, tuple[str, str]]
    clusters: dict[str, str | None]


def _aggs(schema_version: int, include: dict[str, list[str]] | None) -> dict[str, Any]:
    aggs: dict[str, Any] = {}
    for name, needs in KINDS.items():
        keys = include.get(name) if include is not None else None
        if schema_version < needs or (include is not None and not keys):
            continue
        field = _NESTED[name][1] if name in _NESTED else "story_cluster_id"
        terms: dict[str, Any] = {
            "field": field,
            "size": len(keys) if keys else TOP,
            "order": [{"_count": "desc"}, {"_key": "asc"}],
        }
        keyed: dict[str, Any] = {"terms": terms}
        if keys:
            terms["include"] = keys
        else:
            top_hits = {
                "top_hits": {"size": EVIDENCE, "sort": _SORT, "_source": ["article_id", "title"]}
            }
            keyed["aggs"] = (
                {"articles": {"reverse_nested": {}, "aggs": {"hits": top_hits}}}
                if name in _NESTED
                else {"hits": top_hits}
            )
        # Stories are a plain field; the filter wrapper only gives every kind the same `keys` shape.
        wrapper = (
            {"nested": {"path": _NESTED[name][0]}}
            if name in _NESTED
            else {"filter": {"exists": {"field": field}}}
        )
        aggs[name] = {**wrapper, "aggs": {"keys": keyed}}
    return aggs


def window_agg_body(
    query: dict[str, Any], after: datetime | None, upto: datetime, schema_version: int
) -> dict[str, Any]:
    """Top keys per kind among matches first discovered in `(after, upto]`, with sample articles."""
    return {
        "size": 0,
        "track_total_hits": True,
        "query": window_filter(query, after, upto),
        "aggs": _aggs(schema_version, None),
    }


def prior_body(
    query: dict[str, Any], before: datetime, candidates: dict[str, list[str]], schema_version: int
) -> dict[str, Any]:
    """Which of the candidate keys the monitor had already matched at or before `before`."""
    # ponytail: scans this monitor's whole match history (`include` bounds buckets, not documents);
    # add a lookback limit or a per-monitor seen set if a match history ever gets large.
    return {
        "size": 0,
        "track_total_hits": False,
        "query": window_filter(query, None, before),
        "aggs": _aggs(schema_version, candidates),
    }


def parse_found(response: dict[str, Any], name: str) -> tuple[list[Found], bool]:
    keys = response.get("aggregations", {}).get(name, {}).get("keys")
    if keys is None:
        return [], False
    found = []
    for bucket in keys["buckets"]:
        holder = bucket.get("articles", bucket)  # nested kinds count and sample parent articles
        hits = holder.get("hits", {}).get("hits", {}).get("hits", [])
        found.append(
            Found(
                str(bucket["key"]),
                holder["doc_count"] if "articles" in bucket else bucket["doc_count"],
                [
                    ChangeEvidence(
                        article_id=h["_source"]["article_id"], title=h["_source"]["title"]
                    )
                    for h in hits
                ],
            )
        )
    return found, keys["sum_other_doc_count"] > 0


def build_changes(
    after: datetime | None,
    upto: datetime | None,
    total: int,
    *,
    sources: tuple[list[Found], bool],
    entities: tuple[list[Found], bool],
    stories: tuple[list[Found], bool],
    seen: dict[str, set[str]],
    growth: dict[str, tuple[int, int]],
    labels: Labels,
) -> MonitorChanges:
    """Pure: the same facts always give the same statements, in the same order."""
    new_sources = [
        SourceChange(
            source_id=f.key, name=labels.feeds[f.key], article_count=f.count, evidence=f.evidence
        )
        for f in sources[0]
        if f.key not in seen.get("sources", set()) and f.key in labels.feeds
    ]
    new_entities = [
        EntityChange(
            entity_id=f.key,
            name=labels.entities[f.key][0],
            entity_type=labels.entities[f.key][1],
            article_count=f.count,
            evidence=f.evidence,
        )
        for f in entities[0]
        if f.key not in seen.get("entities", set()) and f.key in labels.entities
    ]
    changed: list[StoryChange] = []
    for f in stories[0]:
        if f.key not in labels.clusters:
            continue  # the cluster was dissolved since the index was written
        before, now = growth.get(f.key, (0, 0))
        is_new = f.key not in seen.get("stories", set())
        if is_new or now - before >= MATERIAL_SOURCE_GROWTH:
            changed.append(
                StoryChange(
                    cluster_id=f.key,
                    title=labels.clusters[f.key],
                    status="new" if is_new else "grew",
                    article_count=f.count,
                    source_count=now,
                    sources_added=now - before,
                    evidence=f.evidence,
                )
            )
    return MonitorChanges(
        window_start=after,
        window_end=upto,
        article_count=total,
        sources=new_sources,
        entities=new_entities,
        stories=changed,
        more_sources=sources[1],
        more_entities=entities[1],
        more_stories=stories[1],
    )


async def cluster_growth(
    db: AsyncSession, cluster_ids: list[str], after: datetime, upto: datetime
) -> dict[str, tuple[int, int]]:
    """Distinct sources that had reported a story's current members by `after` and by `upto`."""
    if not cluster_ids:
        return {}
    feed = FeedArticle.feed_id
    rows = await db.execute(
        select(
            StoryClusterMember.cluster_id,
            func.count(distinct(feed)).filter(FeedArticle.discovered_at <= after),
            func.count(distinct(feed)).filter(FeedArticle.discovered_at <= upto),
        )
        .join(FeedArticle, FeedArticle.article_id == StoryClusterMember.article_id)
        .where(StoryClusterMember.cluster_id.in_([uuid.UUID(i) for i in cluster_ids]))
        .group_by(StoryClusterMember.cluster_id)
    )
    return {str(cluster): (before, now) for cluster, before, now in rows}


async def resolve_labels(
    db: AsyncSession, feed_ids: list[str], entity_ids: list[str], cluster_ids: list[str]
) -> Labels:
    def ids(values: list[str]) -> list[uuid.UUID]:
        return [uuid.UUID(v) for v in values]

    feeds = await db.execute(select(Feed.id, Feed.name).where(Feed.id.in_(ids(feed_ids))))
    entities = await db.execute(
        select(Entity.id, Entity.display_text, Entity.entity_type).where(
            Entity.id.in_(ids(entity_ids))
        )
    )
    clusters = await db.execute(
        select(StoryCluster.id, Article.title)
        .outerjoin(Article, Article.id == StoryCluster.representative_article_id)
        .where(StoryCluster.id.in_(ids(cluster_ids)))
    )
    return Labels(
        feeds={str(i): name for i, name in feeds},
        entities={str(i): (text, kind) for i, text, kind in entities},
        clusters={str(i): title for i, title in clusters},
    )


async def monitor_changes(db: AsyncSession, item: Monitor) -> MonitorChanges:
    after, upto = item.viewed_cursor_at, item.eval_cursor_at
    if after is None or upto is None or after >= upto:
        return build_changes(
            after, upto, 0, sources=([], False), entities=([], False), stories=([], False),
            seen={}, growth={}, labels=Labels({}, {}, {}),
        )  # fmt: skip
    state, problem = parse_state(item)
    if state is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "invalid_monitor_state", "message": problem}
        )
    criteria = await search_criteria(db, **criteria_params(state))
    index_name, schema_version = await current_search_target(db, criteria)
    query = build_query(criteria, schema_version)
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    try:
        current = await adapter.search_index(
            index_name, window_agg_body(query, after, upto, schema_version)
        )
        found = {name: parse_found(current, name) for name in KINDS}
        candidates = {name: [f.key for f in items] for name, (items, _) in found.items()}
        prior: dict[str, Any] = {}
        if any(candidates.values()):
            prior = await adapter.search_index(
                index_name, prior_body(query, after, candidates, schema_version)
            )
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
    return build_changes(
        after,
        upto,
        current["hits"]["total"]["value"],
        sources=found["sources"],
        entities=found["entities"],
        stories=found["stories"],
        seen={name: {f.key for f in parse_found(prior, name)[0]} for name in KINDS},
        growth=await cluster_growth(db, candidates["stories"], after, upto),
        labels=await resolve_labels(
            db, candidates["sources"], candidates["entities"], candidates["stories"]
        ),
    )
