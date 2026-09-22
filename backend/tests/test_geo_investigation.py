import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from test_search_results import _criteria

from app.core.config import Settings
from app.feeds.schemas import ArticleResponse
from app.feeds.service import encode_cursor
from app.geo.investigation import (
    MAX_COUNTRIES,
    articles_body,
    countries_body,
    in_country,
    located,
    parse_countries,
)
from app.geo.routes import geo_articles, geo_countries
from app.search.criteria import build_query
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.query import parse_query
from app.search.routes import read_cursor

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


A, B, C, D = (uuid.UUID(int=n) for n in range(1, 5))


class _Session:
    def __init__(self, n: int = 7) -> None:
        self.id = uuid.UUID(int=n)


def _hit(article_id: uuid.UUID, n: int) -> dict[str, Any]:
    return {"_source": {"article_id": str(article_id)}, "sort": [n, str(article_id)]}


def _article(article_id: uuid.UUID) -> ArticleResponse:
    return ArticleResponse(
        id=article_id,
        original_url="https://x.test",
        normalized_url="https://x.test",
        title=str(article_id),
        published_at=None,
        first_discovered_at=datetime(2026, 9, 1, tzinfo=UTC),
        provenance=[],
    )


class _PitAdapter:
    responses: list[dict[str, Any] | Exception] = []
    bodies: list[dict[str, Any]] = []
    closed: list[str] = []

    def __init__(self, _url: str) -> None:
        pass

    async def open_point_in_time(self, _index: str) -> str:
        return "pit-1"

    async def close_point_in_time(self, pit_id: str) -> None:
        _PitAdapter.closed.append(pit_id)

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        _PitAdapter.bodies.append(body)
        result = _PitAdapter.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def paged(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The route's arguments for an investigation page; B's article is gone from PostgreSQL."""
    monkeypatch.setattr("app.geo.routes.ElasticsearchAdapter", _PitAdapter)

    async def found(_db: object, ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
        return {article_id: article_id for article_id in ids if article_id != B}

    monkeypatch.setattr("app.geo.investigation.articles_by_id", found)
    monkeypatch.setattr("app.geo.investigation.article_response", _article)
    _PitAdapter.responses, _PitAdapter.bodies, _PitAdapter.closed = [], [], []
    return {
        "db": _Database(),
        "session": _Session(),
        "settings": Settings(),
        "criteria": _criteria(),
        "role": "story",
        "code": "GR",
        "scope": "investigation",
        "days": None,
        "cursor": None,
        "limit": 2,
    }


def test_evidence_pages_in_the_recent_maps_order_inside_a_snapshot() -> None:
    body = articles_body("story", "GR", QUERY, "pit-1", 30, None)
    assert body["size"] == 31  # the extra hit reveals whether a page follows
    assert body["pit"] == {"id": "pit-1", "keep_alive": "5m"}
    assert body["query"] == {"bool": {"filter": [QUERY, {"term": {"primary_story_country": "GR"}}]}}
    assert body["sort"] == [{"effective_date": "desc"}, {"article_id": "desc"}]
    assert body["_source"] == ["article_id"]
    assert "search_after" not in body
    assert articles_body("story", "GR", QUERY, "p", 2, [5, "x"])["search_after"] == [5, "x"]


@pytest.mark.asyncio
async def test_stale_hits_are_counted_and_the_cursor_moves_past_them(
    paged: dict[str, Any],
) -> None:
    _PitAdapter.responses = [
        {"pit_id": "pit-2", "hits": {"hits": [_hit(A, 3), _hit(B, 2), _hit(C, 1)]}},
        {"pit_id": "pit-3", "hits": {"hits": [_hit(D, 0)]}},
    ]
    first = await geo_articles(**paged)
    assert [item.id for item in first.items] == [A]
    assert first.skipped_stale == 1
    assert first.next_cursor is not None
    data = read_cursor(first.next_cursor, Settings().secret_key)
    assert (data["after"], data["pit"], data["scope"], data["schema"]) == (
        [2, str(B)],
        "pit-2",
        "investigation",
        3,
    )
    assert _PitAdapter.closed == []

    second = await geo_articles(**{**paged, "cursor": first.next_cursor})
    assert _PitAdapter.bodies[1]["search_after"] == [2, str(B)]
    assert _PitAdapter.bodies[1]["pit"]["id"] == "pit-2"
    assert ([item.id for item in second.items], second.next_cursor, second.skipped_stale) == (
        [D],
        None,
        0,
    )
    assert _PitAdapter.closed == ["pit-3"]  # the last page releases its snapshot


@pytest.mark.asyncio
async def test_a_cursor_replays_only_for_its_session_country_criteria_and_page_size(
    paged: dict[str, Any],
) -> None:
    _PitAdapter.responses = [{"hits": {"hits": [_hit(A, 3), _hit(C, 1), _hit(D, 0)]}}]
    cursor = (await geo_articles(**paged)).next_cursor
    assert cursor is not None
    other = _criteria(q="other", parsed=parse_query("other"))
    for change in (
        {"code": "US"},
        {"role": "mentioned"},
        {"limit": 3},
        {"criteria": other},
        {"session": _Session(8)},
    ):
        with pytest.raises(HTTPException) as raised:
            await geo_articles(**{**paged, "cursor": cursor, **change})
        assert raised.value.status_code == 422, change

    # A recent map's keyset cursor is not an investigation cursor, and the reverse.
    legacy = encode_cursor(datetime(2026, 9, 1, tzinfo=UTC), A)
    with pytest.raises(HTTPException) as raised:
        await geo_articles(**{**paged, "cursor": legacy})
    assert raised.value.status_code == 422
    with pytest.raises(HTTPException) as raised:
        await geo_articles(**{**paged, "scope": "recent", "cursor": cursor})
    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_a_released_snapshot_asks_for_a_restart(paged: dict[str, Any]) -> None:
    _PitAdapter.responses = [
        {"hits": {"hits": [_hit(A, 3), _hit(C, 1), _hit(D, 0)]}},
        ElasticsearchUnavailable("404 No search context found for point in time"),
    ]
    cursor = (await geo_articles(**paged)).next_cursor
    with pytest.raises(HTTPException) as raised:
        await geo_articles(**{**paged, "cursor": cursor})
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "restart_search"


@pytest.mark.asyncio
async def test_a_close_failure_still_returns_the_last_page(
    paged: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(_self: object, _pit: str) -> None:
        raise ElasticsearchUnavailable("down")

    monkeypatch.setattr(_PitAdapter, "close_point_in_time", fail)
    _PitAdapter.responses = [{"hits": {"hits": [_hit(A, 3)]}}]
    page = await geo_articles(**paged)
    assert ([item.id for item in page.items], page.next_cursor) == ([A], None)
