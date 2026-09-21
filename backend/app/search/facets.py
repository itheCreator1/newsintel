"""Bounded facet counts for one investigation; every count is matching root articles."""

from dataclasses import dataclass
from typing import Any

from app.search.elasticsearch import ElasticsearchUnavailable

MAX_FACET_BUCKETS = 25
ORDER = [{"_count": "desc"}, {"_key": "asc"}]
ROOT_ORDER = [{"articles": "desc"}, {"_key": "asc"}]
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


class PartialResponse(ElasticsearchUnavailable):
    """A 200 that timed out or lost shards: its counts would be silently low."""


def facets_body(query: dict[str, Any], limit: int) -> dict[str, Any]:
    size = min(limit, MAX_FACET_BUCKETS)
    aggs: dict[str, Any] = {}
    for name, (path, field) in GROUPS.items():
        if path is None:
            aggs[name] = {"terms": {"field": field, "size": size, "order": ORDER}}
            continue
        # An article can hold several records per value (two ORG entities, two feeds in one
        # country), so count and rank by the root articles above them, not the records.
        aggs[name] = {
            "nested": {"path": path},
            "aggs": {
                "top": {
                    "terms": {"field": field, "size": size, "order": ROOT_ORDER},
                    "aggs": {"articles": {"reverse_nested": {}}},
                }
            },
        }
    return {"size": 0, "track_total_hits": True, "query": query, "aggs": aggs}


def ensure_complete(response: dict[str, Any]) -> None:
    # ponytail: only facets check this; graph, timeline and monitors read aggregations
    # unguarded, move it into ElasticsearchAdapter.search_index once another view
    # promises exact counts.
    if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
        raise PartialResponse("Elasticsearch returned partial results")


@dataclass(frozen=True)
class Counted:
    buckets: list[tuple[str, int]]
    truncated: bool


def parse_facets(response: dict[str, Any]) -> tuple[int, dict[str, Counted]]:
    ensure_complete(response)
    aggregations = response.get("aggregations", {})
    groups: dict[str, Counted] = {}
    for name, (path, _field) in GROUPS.items():
        agg = aggregations.get(name, {})
        top = agg.get("top", {}) if path else agg
        groups[name] = Counted(
            buckets=[
                (
                    str(bucket["key"]),
                    int((bucket["articles"] if path else bucket)["doc_count"]),
                )
                for bucket in top.get("buckets", [])
            ],
            truncated=int(top.get("sum_other_doc_count", 0)) > 0,
        )
    return int(response["hits"]["total"]["value"]), groups
