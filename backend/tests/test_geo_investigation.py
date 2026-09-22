from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from test_search_results import _criteria

from app.core.config import Settings
from app.geo.investigation import (
    MAX_COUNTRIES,
    countries_body,
    in_country,
    located,
    parse_countries,
)
from app.geo.routes import geo_countries
from app.search.criteria import build_query
from app.search.query import parse_query

QUERY: dict[str, Any] = {"match_all": {}}
STORIES = {"cardinality": {"field": "story_cluster_id", "precision_threshold": 3000}}
STORY_RESPONSE: dict[str, Any] = {
    "hits": {"total": {"value": 9, "relation": "eq"}},
    "aggregations": {
        "countries": {
            "buckets": [
                {"key": "GR", "doc_count": 4, "stories": {"value": 3}},
                {"key": "US", "doc_count": 2, "stories": {"value": 2}},
            ]
        },
        "located": {"doc_count": 5},
    },
}


def test_story_and_mentioned_count_documents_by_their_own_field_and_estimate_stories() -> None:
    body = countries_body("mentioned", QUERY)
    assert (body["size"], body["track_total_hits"], body["query"]) == (0, True, QUERY)
    countries = body["aggs"]["countries"]
    assert countries["terms"] == {
        "field": "mentioned_countries",
        "size": MAX_COUNTRIES,
        "order": [{"_count": "desc"}, {"_key": "asc"}],
    }
    assert countries["aggs"] == {"stories": STORIES}
    assert body["aggs"]["located"] == {"filter": {"exists": {"field": "mentioned_countries"}}}
    story = countries_body("story", QUERY)["aggs"]["countries"]["terms"]
    assert story["field"] == "primary_story_country"


def test_source_counts_root_articles_under_the_nested_feeds() -> None:
    body = countries_body("source", QUERY)
    countries = body["aggs"]["countries"]
    assert countries["nested"] == {"path": "provenance"}
    top = countries["aggs"]["top"]
    assert top["terms"] == {
        "field": "provenance.source_country",
        "size": 300,
        "order": [{"articles": "desc"}, {"_key": "asc"}],
    }
    assert top["aggs"]["articles"] == {"reverse_nested": {}, "aggs": {"stories": STORIES}}
    assert top["aggs"]["sources"] == {
        "cardinality": {"field": "provenance.source_id", "precision_threshold": 3000}
    }
    assert body["aggs"]["located"] == {"filter": located("source")}
    assert located("source") == {
        "nested": {
            "path": "provenance",
            "query": {"exists": {"field": "provenance.source_country"}},
        }
    }


def test_parse_reads_exact_articles_and_coverage_and_estimated_stories() -> None:
    coverage, items = parse_countries("story", STORY_RESPONSE)
    assert (coverage.unit, coverage.window_total, coverage.located) == ("articles", 9, 5)
    assert [(i.country_code, i.articles, i.stories, i.sources, i.events) for i in items] == [
        ("GR", 4, 3, None, None),
        ("US", 2, 2, None, None),
    ]


def test_parse_source_takes_articles_from_the_root_not_the_feed_records() -> None:
    response = {
        "hits": {"total": {"value": 3}},
        "aggregations": {
            "countries": {
                "doc_count": 7,
                "top": {
                    "buckets": [
                        {
                            "key": "US",
                            "doc_count": 5,  # feed records: one article can hold two US feeds
                            "articles": {"doc_count": 3, "stories": {"value": 2}},
                            "sources": {"value": 2},
                        }
                    ]
                },
            },
            "located": {"doc_count": 3},
        },
    }
    _, items = parse_countries("source", response)
    assert [(i.country_code, i.articles, i.stories, i.sources) for i in items] == [("US", 3, 2, 2)]


def test_an_empty_index_parses_to_no_countries() -> None:
    coverage, items = parse_countries("story", {"hits": {"total": {"value": 0}}})
    assert (coverage.window_total, coverage.located, items) == (0, 0, [])


def test_evidence_filters_on_the_role_field() -> None:
    assert in_country("story", "GR") == {"term": {"primary_story_country": "GR"}}
    assert in_country("mentioned", "GR") == {"term": {"mentioned_countries": "GR"}}
    assert in_country("source", "GR") == {
        "nested": {"path": "provenance", "query": {"term": {"provenance.source_country": "GR"}}}
    }


class _Target:
    def __init__(self, schema_version: int = 3) -> None:
        self.index_name = f"articles-v{schema_version}-geo"
        self.schema_version = schema_version


class _Database:
    def __init__(self, schema_version: int = 3) -> None:
        self.target = _Target(schema_version)

    async def scalar(self, _query: object) -> _Target:
        return self.target


class _Adapter:
    response: dict[str, Any] = {}
    body: dict[str, Any] = {}
    index = ""

    def __init__(self, _url: str) -> None:
        pass

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        _Adapter.index, _Adapter.body = index_name, body
        return _Adapter.response


def test_empty_criteria_are_only_display_preferences_away() -> None:
    assert _criteria().empty
    assert not _criteria(q="grid", parsed=parse_query("grid")).empty
    assert not _criteria(start=date(2026, 9, 1)).empty
    assert not _criteria(content_available=False).empty
    assert not _criteria(story_cluster_ids=["c1"]).empty


@pytest.mark.asyncio
async def test_investigation_counts_come_from_the_index_with_bounds_and_estimates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.geo.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = STORY_RESPONSE
    criteria = _criteria(q="grid", parsed=parse_query("grid"), start=date(2026, 9, 1))
    body = await geo_countries(
        _Database(),
        None,
        Settings(),
        criteria,
        "story",
        "investigation",
        None,  # type: ignore[arg-type]
    )
    assert _Adapter.index == "articles-v3-geo"
    # The criteria alone: no rolling window is added, and the selection never reaches the base.
    assert _Adapter.body == countries_body("story", build_query(criteria, 3))
    assert (body.scope, body.days, body.window_start, body.window_end) == (
        "investigation",
        None,
        datetime(2026, 9, 1, tzinfo=UTC),
        None,
    )
    assert (body.stories_estimated, body.sources_estimated) == (True, False)
    assert (body.coverage.window_total, body.coverage.located) == (9, 5)
    assert [item.country_code for item in body.items] == ["GR", "US"]


@pytest.mark.asyncio
async def test_only_source_maps_estimate_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.geo.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = {"hits": {"total": {"value": 0}}}
    body = await geo_countries(
        _Database(),
        None,
        Settings(),
        _criteria(),
        "source",
        "investigation",
        None,  # type: ignore[arg-type]
    )
    assert (body.stories_estimated, body.sources_estimated) == (True, True)


@pytest.mark.asyncio
async def test_an_index_below_schema_three_needs_a_rebuild_even_with_no_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.geo.routes.ElasticsearchAdapter", _Adapter)
    with pytest.raises(HTTPException) as raised:
        await geo_countries(
            _Database(2),
            None,
            Settings(),
            _criteria(),
            "story",
            "investigation",
            None,  # type: ignore[arg-type]
        )
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "search_upgrade_required"


@pytest.mark.asyncio
async def test_a_partial_response_is_unavailable_not_low(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.geo.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = {**STORY_RESPONSE, "timed_out": True}
    with pytest.raises(HTTPException) as raised:
        await geo_countries(
            _Database(),
            None,
            Settings(),
            _criteria(),
            "story",
            "investigation",
            None,  # type: ignore[arg-type]
        )
    assert raised.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "scope", "days", "criteria", "code"),
    [
        (
            "story",
            "recent",
            None,
            _criteria(q="grid", parsed=parse_query("grid")),
            "investigation_scope_required",
        ),
        ("event", "recent", 30, _criteria(countries=["GR"]), "investigation_scope_required"),
        ("story", "investigation", 30, _criteria(), "days_not_in_investigation"),
        ("event", "investigation", None, _criteria(), "event_scope_unsupported"),
    ],
)
async def test_the_two_modes_never_mix(
    role: str, scope: str, days: int | None, criteria: Any, code: str
) -> None:
    with pytest.raises(HTTPException) as raised:
        # Refused before any database or index work, so the database is a bare object.
        await geo_countries(
            object(),
            None,
            Settings(),
            criteria,
            role,
            scope,
            days,  # type: ignore[arg-type]
        )
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == code
