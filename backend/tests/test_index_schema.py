"""Every request body the backend builds must resolve against the V3 mapping.

Elasticsearch matches nothing, silently, for an unmapped field, a nested field queried outside its
`nested` wrapper, or a root field queried inside one; no runtime error would reveal any of them.
"""

import hashlib
import json
from datetime import date
from typing import Any

from test_search_timeline import _criteria

from app.search.criteria import SearchCriteria
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.rebuild import SCHEMA_VERSION

V3 = ARTICLE_INDEX_SETTINGS_V3["mappings"]["properties"]
# Query clauses whose keys are field names; with a "field" key the same names are aggregations.
_LEAF = {"term", "terms", "range", "match", "match_phrase", "prefix", "wildcard"}
# Keys whose values are never field references (bucket order, include lists, paging, bounds).
_OPAQUE = {"order", "include", "exclude", "pit", "search_after", "extended_bounds"}

ENTITY = "66666666-6666-4666-8666-666666666666"
KEYWORD = "77777777-7777-4777-8777-777777777777"
CLUSTER = "88888888-8888-4888-8888-888888888888"
SOURCE = "99999999-9999-4999-8999-999999999999"


def _nested_of(properties: dict[str, Any], field: str) -> str | None:
    """The deepest nested path above `field` ("" at the root), or None when it is not mapped."""
    node: dict[str, Any] = {"properties": properties}
    nested, parts = "", []
    for part in field.split("."):
        found = node.get("properties", {}).get(part)
        if found is None:
            return None
        node = found
        parts.append(part)
        if node.get("type") == "nested":
            nested = ".".join(parts)
    return nested


def unmapped(body: dict[str, Any], properties: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def ref(field: str, context: str, where: str, *, placed: bool = True) -> None:
        nested = _nested_of(properties, field)
        if nested is None:
            errors.append(f"{where}: {field} is not mapped")
        elif placed and nested != context:
            errors.append(
                f"{where}: {field} lives under '{nested or 'root'}', used in '{context or 'root'}'"
            )

    def path(value: str, where: str) -> str:
        if _nested_of(properties, value) != value:
            errors.append(f"{where}: {value} is not a nested path")
        return value

    def walk(node: Any, context: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, context)
            return
        if not isinstance(node, dict):
            return
        # A bucket's sub-aggregations run in the context its own aggregation sets.
        children = context
        if isinstance(node.get("nested"), dict) and "query" not in node["nested"]:
            children = path(node["nested"]["path"], "nested aggregation")
        if isinstance(node.get("reverse_nested"), dict):
            children = node["reverse_nested"].get("path", "")
        for key, value in node.items():
            if key in _OPAQUE:
                continue
            if key in _LEAF and isinstance(value, dict) and "field" not in value:
                for field in value:
                    if field != "boost":
                        ref(field, context, key)
            elif key == "multi_match":
                for field in value["fields"]:
                    ref(field.split("^")[0], context, key)
            elif key == "field" and isinstance(value, str):
                ref(value, context, "field")
            elif key == "nested" and "query" in value:
                walk(value["query"], path(value["path"], "nested query"))
            elif key == "sort":
                for item in value:
                    for field in [item] if isinstance(item, str) else item:
                        if not field.startswith("_"):
                            ref(field, context, "sort")
            elif key == "_source":
                # Source filtering may name an object or nested field itself (e.g. "provenance").
                for field in value:
                    ref(field, context, "_source", placed=False)
            elif key == "highlight":
                for field in value["fields"]:
                    ref(field, context, "highlight")
            elif key == "aggs":
                walk(value, children)
            else:
                walk(value, context)

    walk(body, "")
    return errors


def _maximal() -> SearchCriteria:
    """Every criterion set, so every filter clause build_query can emit is present."""
    return _criteria(
        'grid "power cut"',
        sources=[SOURCE],
        countries=["GR"],
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        content_available=True,
        processing=["processed"],
        languages=["en"],
        entity_ids=[ENTITY],
        entity_types=["ORG"],
        keyword_ids=[KEYWORD],
        story_countries=["GR"],
        mentioned_countries=["UA"],
        story_cluster_ids=[CLUSTER],
    )


def test_a_field_the_mapping_lacks_is_reported() -> None:
    assert unmapped({"query": {"term": {"source_country": "GR"}}}, V3) == [
        "term: source_country is not mapped"
    ]


def test_a_nested_field_queried_at_the_root_is_reported() -> None:
    assert unmapped({"query": {"terms": {"entities.id": [ENTITY]}}}, V3) == [
        "terms: entities.id lives under 'entities', used in 'root'"
    ]


def test_a_root_field_queried_inside_a_nested_clause_is_reported() -> None:
    body = {
        "query": {"nested": {"path": "provenance", "query": {"term": {"detected_language": "en"}}}}
    }
    assert unmapped(body, V3) == [
        "term: detected_language lives under 'root', used in 'provenance'"
    ]


def test_a_nested_path_that_is_only_an_object_is_reported() -> None:
    body = {
        "aggs": {
            "k": {
                "nested": {"path": "keywords"},
                "aggs": {"t": {"terms": {"field": "keywords.id"}}},
            }
        }
    }
    assert unmapped(body, V3)[0] == "nested aggregation: keywords is not a nested path"


def test_a_nested_field_aggregated_after_reverse_nested_is_reported() -> None:
    body = {
        "aggs": {
            "c": {
                "nested": {"path": "provenance"},
                "aggs": {
                    "top": {
                        "terms": {"field": "provenance.source_country"},
                        "aggs": {
                            "articles": {
                                "reverse_nested": {},
                                "aggs": {
                                    "feeds": {"cardinality": {"field": "provenance.source_id"}}
                                },
                            }
                        },
                    }
                },
            }
        }
    }
    assert unmapped(body, V3) == [
        "field: provenance.source_id lives under 'provenance', used in 'root'"
    ]


def test_text_sort_source_and_highlight_fields_are_checked() -> None:
    body = {
        "query": {"multi_match": {"query": "grid", "fields": ["title^3", "summary"]}},
        "sort": [{"_score": "desc"}, {"published": "desc"}],
        "_source": ["article_id", "provenance", "headline"],
        "highlight": {"fields": {"title": {}, "lede": {}}},
    }
    assert unmapped(body, V3) == [
        "multi_match: summary is not mapped",
        "sort: published is not mapped",
        "_source: headline is not mapped",
        "highlight: lede is not mapped",
    ]


# ponytail: a hash, not a diff; on failure compare ARTICLE_INDEX_SETTINGS_V3 with git history.
V3_SHA256 = "fcd853441f9f4e7e9743243f597f2d1723a7a5024ac9a9872b2d940ebc71dadd"


def test_v3_is_frozen_and_current() -> None:
    digest = hashlib.sha256(
        json.dumps(ARTICLE_INDEX_SETTINGS_V3, sort_keys=True).encode()
    ).hexdigest()
    assert digest == V3_SHA256, (
        "V3 is deployed: never change it (or V1/V2, which it spreads) in place. Add "
        "ARTICLE_INDEX_SETTINGS_V4, bump SCHEMA_VERSION and cut over through a replacement index."
    )
    assert SCHEMA_VERSION == 3
