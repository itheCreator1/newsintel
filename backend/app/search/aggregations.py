"""Bounded aggregation builders and parsers shared by search facets and analytics."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.search.elasticsearch import ElasticsearchUnavailable

ORDER = [{"_count": "desc"}, {"_key": "asc"}]
ROOT_ORDER = [{"articles": "desc"}, {"_key": "asc"}]


class PartialResponse(ElasticsearchUnavailable):
    """A 200 that timed out or lost shards: its counts would be silently low."""


def ensure_complete(response: dict[str, Any]) -> None:
    # ponytail: facets and analytics check this; graph, the search timeline and monitors read
    # aggregations unguarded, move it into ElasticsearchAdapter.search_index once one of them
    # promises exact counts.
    if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
        raise PartialResponse("Elasticsearch returned partial results")


@dataclass(frozen=True)
class Counted:
    buckets: list[tuple[str, int]]
    truncated: bool


def terms(
    path: str | None, field: str, size: int, *, within: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The top `size` values of `field`; `within` keeps only matching nested records."""
    if path is None:
        return {"terms": {"field": field, "size": size, "order": ORDER}}
    # An article can hold several records per value (two ORG entities, two feeds in one
    # country), so count and rank by the root articles above them, not the records.
    top = {
        "terms": {"field": field, "size": size, "order": ROOT_ORDER},
        "aggs": {"articles": {"reverse_nested": {}}},
    }
    if within is None:
        return {"nested": {"path": path}, "aggs": {"top": top}}
    return {
        "nested": {"path": path},
        "aggs": {"matching": {"filter": within, "aggs": {"top": top}}},
    }


def read_terms(agg: dict[str, Any], nested: bool) -> Counted:
    if nested:
        agg = agg.get("matching", agg).get("top", {})
    return Counted(
        buckets=[
            (str(bucket["key"]), int((bucket["articles"] if nested else bucket)["doc_count"]))
            for bucket in agg.get("buckets", [])
        ],
        truncated=int(agg.get("sum_other_doc_count", 0)) > 0,
    )


def date_histogram(field: str, interval: str, first: datetime, last: datetime) -> dict[str, Any]:
    """Every UTC `interval` bucket from `first` to `last`, empty ones included."""
    return {
        "date_histogram": {
            "field": field,
            "calendar_interval": interval,
            "time_zone": "UTC",
            "min_doc_count": 0,
            "extended_bounds": {
                "min": int(first.timestamp() * 1000),
                "max": int(last.timestamp() * 1000),
            },
        }
    }
