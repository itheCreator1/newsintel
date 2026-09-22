"""Investigation-mode map counts and evidence, from Elasticsearch over the shared search criteria.

Recent maps stay in PostgreSQL (`app/geo/queries.py`), exact. Here articles per country and the
coverage base are document and filter counts, exact over the index; stories and sources are
cardinality estimates and the response flags them. Roles are never combined.
"""

from datetime import UTC, date, datetime, time
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.schemas import ArticleRole, GeoCountriesResponse, GeoCountry, GeoCoverage
from app.search.aggregations import ORDER, ROOT_ORDER, ensure_complete
from app.search.criteria import SearchCriteria, build_query, current_search_target
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

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
