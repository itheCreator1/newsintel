"""Investigation-mode map counts and evidence, from Elasticsearch over the shared search criteria.

Recent maps stay in PostgreSQL (`app/geo/queries.py`), exact. Here articles per country and the
coverage base are document and filter counts, exact over the index; stories and sources are
cardinality estimates and the response flags them. Roles are never combined.
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.feeds.service import article_response
from app.geo.schemas import (
    ArticleRole,
    GeoArticlePage,
    GeoCountriesResponse,
    GeoCountry,
    GeoCoverage,
)
from app.graph.service import articles_by_id
from app.search.aggregations import ORDER, ROOT_ORDER, ensure_complete
from app.search.criteria import SearchCriteria, build_query, current_search_target
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable
from app.search.routes import read_cursor, sign_cursor

# Stories count distinct `story_cluster_id`, a schema 3 field; the floor is explicit because
# empty criteria never trip `current_search_target`'s own annotation checks.
SCHEMA_FLOOR = 3
# ponytail: no truncation flag, ~250 ISO codes fit; add one if codes ever outgrow the cap.
MAX_COUNTRIES = 300
# Cardinality is near-exact below this many distinct values per country, approximate above.
PRECISION = 3000

_FIELD = {"story": "primary_story_country", "mentioned": "mentioned_countries"}
_STORIES = {"cardinality": {"field": "story_cluster_id", "precision_threshold": PRECISION}}


def located(role: ArticleRole) -> dict[str, Any]:
    """Articles with any country in `role`; the source country lives on the nested feeds."""
    if role == "source":
        return {
            "nested": {
                "path": "provenance",
                "query": {"exists": {"field": "provenance.source_country"}},
            }
        }
    return {"exists": {"field": _FIELD[role]}}


def in_country(role: ArticleRole, code: str) -> dict[str, Any]:
    """Articles with `code` in `role`."""
    if role == "source":
        return {
            "nested": {"path": "provenance", "query": {"term": {"provenance.source_country": code}}}
        }
    return {"term": {_FIELD[role]: code}}


def countries_body(role: ArticleRole, query: dict[str, Any]) -> dict[str, Any]:
    """Per-country counts and the coverage base; `hits.total` is every matching article."""
    countries: dict[str, Any]
    if role == "source":
        # An article from two feeds in one country counts once: rank by the root articles.
        countries = {
            "nested": {"path": "provenance"},
            "aggs": {
                "top": {
                    "terms": {
                        "field": "provenance.source_country",
                        "size": MAX_COUNTRIES,
                        "order": ROOT_ORDER,
                    },
                    "aggs": {
                        "articles": {"reverse_nested": {}, "aggs": {"stories": _STORIES}},
                        "sources": {
                            "cardinality": {
                                "field": "provenance.source_id",
                                "precision_threshold": PRECISION,
                            }
                        },
                    },
                }
            },
        }
    else:
        countries = {
            "terms": {"field": _FIELD[role], "size": MAX_COUNTRIES, "order": ORDER},
            "aggs": {"stories": _STORIES},
        }
    return {
        "size": 0,
        "track_total_hits": True,
        "query": query,
        "aggs": {"countries": countries, "located": {"filter": located(role)}},
    }


def parse_countries(
    role: ArticleRole, response: dict[str, Any]
) -> tuple[GeoCoverage, list[GeoCountry]]:
    aggregations = response.get("aggregations", {})
    countries = aggregations.get("countries", {})
    buckets = countries.get("top", countries).get("buckets", [])
    items = []
    for bucket in buckets:
        root = bucket["articles"] if role == "source" else bucket
        items.append(
            GeoCountry(
                country_code=str(bucket["key"]),
                articles=int(root["doc_count"]),
                stories=int(root["stories"]["value"]),
                sources=int(bucket["sources"]["value"]) if role == "source" else None,
                events=None,
            )
        )
    coverage = GeoCoverage(
        unit="articles",
        window_total=int(response.get("hits", {}).get("total", {}).get("value", 0)),
        located=int(aggregations.get("located", {}).get("doc_count", 0)),
    )
    return coverage, items


def _midnight(day: date | None) -> datetime | None:
    return datetime.combine(day, time.min, UTC) if day else None


async def countries(
    db: AsyncSession, adapter: ElasticsearchAdapter, role: ArticleRole, criteria: SearchCriteria
) -> GeoCountriesResponse:
    index_name, schema_version = await current_search_target(db, criteria, minimum=SCHEMA_FLOOR)
    try:
        response = await adapter.search_index(
            index_name, countries_body(role, build_query(criteria, schema_version))
        )
        ensure_complete(response)
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The map is unavailable") from exc
    coverage, items = parse_countries(role, response)
    return GeoCountriesResponse(
        role=role,
        scope="investigation",
        days=None,
        window_start=_midnight(criteria.start),
        window_end=_midnight(criteria.end),
        coverage=coverage,
        items=items,
        stories_estimated=True,
        sources_estimated=role == "source",
    )


# The recent map's evidence order, so both modes list a country the same way.
EVIDENCE_SORT = [{"effective_date": "desc"}, {"article_id": "desc"}]


def articles_body(
    role: ArticleRole,
    code: str,
    query: dict[str, Any],
    pit_id: str,
    limit: int,
    after: list[Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "size": limit + 1,  # the extra hit reveals whether a page follows
        "pit": {"id": pit_id, "keep_alive": "5m"},
        "query": {"bool": {"filter": [query, in_country(role, code)]}},
        "sort": EVIDENCE_SORT,
        "_source": ["article_id"],
        "track_total_hits": False,
    }
    if after:
        body["search_after"] = after
    return body


def _binding(role: ArticleRole, code: str, criteria: SearchCriteria, limit: int) -> str:
    fingerprint = {**criteria.fingerprint(), "role": role, "code": code, "limit": limit}
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()


def _restart() -> HTTPException:
    return HTTPException(409, {"code": "restart_search", "message": "Map snapshot expired"})


async def articles(
    db: AsyncSession,
    adapter: ElasticsearchAdapter,
    secret: str,
    session_id: uuid.UUID,
    role: ArticleRole,
    code: str,
    criteria: SearchCriteria,
    limit: int,
    cursor: str | None,
) -> GeoArticlePage:
    binding = _binding(role, code, criteria, limit)
    after: list[Any] | None = None
    if cursor:
        data = read_cursor(cursor, secret)
        if (data.get("scope"), data.get("session"), data.get("criteria")) != (
            "investigation",
            str(session_id),
            binding,
        ):
            raise HTTPException(422, "Map cursor does not match this session, country or view")
        if datetime.fromisoformat(data["expires"]) <= datetime.now(UTC):
            raise _restart()
        pit_id, after, schema_version = data["pit"], data["after"], int(data["schema"])
    else:
        index_name, schema_version = await current_search_target(db, criteria, minimum=SCHEMA_FLOOR)
    try:
        if not cursor:
            pit_id = await adapter.open_point_in_time(index_name)
        query = build_query(criteria, schema_version)
        # ponytail: no ensure_complete() here, unlike the counts request above. A shard failure
        # silently drops evidence rows (not just a count), and len(found) <= limit below can then
        # end paging early, closing the PIT on what looks like the last page. Upgrade path: call
        # ensure_complete on this response too.
        response = await adapter.search(articles_body(role, code, query, pit_id, limit, after))
    except ElasticsearchUnavailable as exc:
        message = str(exc)
        if cursor and ("404" in message or "point in time" in message.lower()):
            raise _restart() from exc
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The map is unavailable") from exc
    found = response.get("hits", {}).get("hits", [])
    hits = found[:limit]
    pit_id = response.get("pit_id", pit_id)
    ids = [uuid.UUID(hit["_source"]["article_id"]) for hit in hits]
    rows = await articles_by_id(db, ids)
    # A hit whose article is gone is skipped, not refilled: the cursor still advances past it,
    # so one page costs one index request however stale the projection is.
    items = [article_response(rows[article_id]) for article_id in ids if article_id in rows]
    next_cursor = None
    if len(found) <= limit:
        # The last page: release the snapshot now; keep_alive only covers abandoned pages.
        try:
            await adapter.close_point_in_time(pit_id)
        except ElasticsearchUnavailable:
            pass
    else:
        payload = {
            "scope": "investigation",
            "session": str(session_id),
            "criteria": binding,
            "pit": pit_id,
            "after": hits[-1]["sort"],
            "schema": schema_version,
            "expires": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        }
        next_cursor = sign_cursor(payload, secret)
    return GeoArticlePage(items=items, next_cursor=next_cursor, skipped_stale=len(ids) - len(items))
