"""Related coverage: other articles worded like this one, never this article's own story.

Elasticsearch `more_like_this` proposes candidates and PostgreSQL decides what is returned. The
story exclusion is rechecked after hydration because the index can lag a clustering change.
"""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryClusterMember
from app.feeds.models import Article
from app.feeds.service import article_response
from app.graph.service import articles_by_id
from app.search.aggregations import ensure_complete
from app.search.criteria import current_search_target
from app.search.elasticsearch import ElasticsearchAdapter
from app.search.schemas import RelatedCoverage, RelatedCoverageItem

MAX_RELATED = 20
# Hits examined per request: enough to survive the story recheck, bounded either way.
CANDIDATES = 100
FIELDS = ["title", "descriptions", "body"]
# Lucene's English stop set. The standard analyzer indexes these words; without this list any two
# English articles share enough of them to look alike.
# ponytail: English only, add per-language lists when non-English feeds need related coverage.
STOP_WORDS = [
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these",
    "they", "this", "to", "was", "will", "with",
]  # fmt: skip
# A candidate must share at least this many of the article's (up to 25) most distinctive terms.
# An absolute count, not a percentage: an article with fewer usable terms matches nothing instead
# of one shared word passing for similarity.
MIN_SHARED_TERMS = 5
# Term and document frequency floors of 1 suit short feed text and small archives; the defaults
# (2 and 5) find nothing in either.
SIMILARITY: dict[str, Any] = {
    "min_term_freq": 1,
    "min_doc_freq": 1,
    "max_query_terms": 25,
    "min_word_length": 3,
    "minimum_should_match": str(MIN_SHARED_TERMS),
}


def related_body(article_id: uuid.UUID, cluster_id: uuid.UUID | None) -> dict[str, Any]:
    exclude: list[dict[str, Any]] = [{"term": {"article_id": str(article_id)}}]
    if cluster_id is not None:
        exclude.append({"term": {"story_cluster_id": str(cluster_id)}})
    similar = {
        "fields": FIELDS,
        "like": [{"_id": str(article_id)}],
        "stop_words": STOP_WORDS,
        **SIMILARITY,
    }
    return {
        "size": CANDIDATES,
        "query": {"bool": {"must": [{"more_like_this": similar}], "must_not": exclude}},
        # Equal scores fall back to the ID, so the candidate budget cuts deterministically.
        "sort": [{"_score": "desc"}, {"article_id": "asc"}],
        "_source": ["article_id"],
        "track_total_hits": False,
    }


async def related_articles(
    db: AsyncSession, adapter: ElasticsearchAdapter, article_id: uuid.UUID, limit: int
) -> RelatedCoverage:
    if await db.get(Article, article_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    cluster_id = await db.scalar(
        select(StoryClusterMember.cluster_id).where(StoryClusterMember.article_id == article_id)
    )
    # The story exclusion reads `story_cluster_id`, a schema 3 field.
    index_name, _schema = await current_search_target(db, None, minimum=3)
    response = await adapter.search_index(index_name, related_body(article_id, cluster_id))
    ensure_complete(response)
    scores = {
        uuid.UUID(hit["_source"]["article_id"]): float(hit["sort"][0])
        for hit in response.get("hits", {}).get("hits", [])
    }
    same_story: set[uuid.UUID] = set()
    if cluster_id is not None and scores:
        same_story = set(
            (
                await db.scalars(
                    select(StoryClusterMember.article_id).where(
                        StoryClusterMember.cluster_id == cluster_id,
                        StoryClusterMember.article_id.in_(list(scores)),
                    )
                )
            ).all()
        )
    kept = [candidate for candidate in scores if candidate not in same_story][:limit]
    rows = await articles_by_id(db, kept)
    items = [
        RelatedCoverageItem(article=article_response(rows[candidate]), score=scores[candidate])
        for candidate in kept
        if candidate in rows
    ]
    return RelatedCoverage(items=items, skipped_stale=len(same_story) + len(kept) - len(items))
