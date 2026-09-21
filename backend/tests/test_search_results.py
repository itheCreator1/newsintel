from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.search.criteria import SearchCriteria, current_search_target
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.query import parse_query
from app.search.routes import read_cursor, search_articles

WIRE = "11111111-1111-4111-8111-111111111111"
DAILY = "22222222-2222-4222-8222-222222222222"


class _Target:
    index_name = "articles-v2-test"
    schema_version = 2


class _Database:
    async def scalar(self, _query: object) -> _Target:
        return _Target()


class _Session:
    id = UUID("33333333-3333-4333-8333-333333333333")


class _Adapter:
    response: dict[str, Any] = {}
    body: dict[str, Any] = {}
    closed: list[str] = []
    close_error: Exception | None = None

    def __init__(self, _url: str) -> None:
        pass

    async def open_point_in_time(self, _alias: str) -> str:
        return "pit"

    async def close_point_in_time(self, pit_id: str) -> None:
        _Adapter.closed.append(pit_id)
        if _Adapter.close_error is not None:
            raise _Adapter.close_error

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        _Adapter.body = body
        return _Adapter.response


def _criteria(**overrides: object) -> SearchCriteria:
    values: dict[str, object] = {
        "parsed": parse_query(""),
        "q": "",
        "sources": [],
        "countries": [],
        "start": None,
        "end": None,
        "content_available": None,
        "processing": [],
        "languages": [],
        "entity_ids": [],
        "entity_types": [],
        "keyword_ids": [],
        "story_countries": [],
        "mentioned_countries": [],
        "story_cluster_ids": [],
    }
    values.update(overrides)
    return SearchCriteria(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_results_carry_filterable_source_and_story_country_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "article_id": "44444444-4444-4444-8444-444444444444",
                        "title": "Grid",
                        "effective_date": "2026-09-14T12:00:00Z",
                        "distinct_source_count": 2,
                        "primary_story_country": "DE",
                        "provenance": [
                            {"source_id": WIRE, "source_name": "Wire", "source_country": "US"},
                            {"source_id": DAILY, "source_name": "Daily", "source_country": None},
                            {"source_id": WIRE, "source_name": "Wire", "source_country": "US"},
                        ],
                    },
                    "sort": [1],
                }
            ]
        }
    }

    page = await search_articles(
        _Database(),  # type: ignore[arg-type]
        _Session(),  # type: ignore[arg-type]
        Settings(),
        _criteria(),
    )

    result = page.items[0]
    assert result.sources == ["Daily", "Wire"]
    assert [(str(ref.id), ref.name, ref.country) for ref in result.source_refs] == [
        (DAILY, "Daily", None),
        (WIRE, "Wire", "US"),
    ]
    assert result.story_country == "DE"
    assert "primary_story_country" in _Adapter.body["_source"]


CLUSTER = "55555555-5555-4555-8555-555555555555"


@pytest.mark.asyncio
async def test_results_carry_a_story_cluster_reference_when_the_article_is_clustered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "article_id": "44444444-4444-4444-8444-444444444444",
                        "title": "Grid",
                        "effective_date": "2026-09-14T12:00:00Z",
                        "distinct_source_count": 2,
                        "story_cluster_id": CLUSTER,
                        "cluster_source_count": 2,
                        "provenance": [
                            {"source_id": WIRE, "source_name": "Wire", "source_country": "US"}
                        ],
                    },
                    "sort": [1],
                }
            ]
        }
    }

    page = await search_articles(
        _Database(),  # type: ignore[arg-type]
        _Session(),  # type: ignore[arg-type]
        Settings(),
        _criteria(),
    )

    result = page.items[0]
    assert result.story_cluster is not None
    assert (str(result.story_cluster.id), result.story_cluster.source_count) == (CLUSTER, 2)
    assert "story_cluster_id" in _Adapter.body["_source"]
    assert "cluster_source_count" in _Adapter.body["_source"]


@pytest.mark.asyncio
async def test_results_have_no_story_cluster_when_the_article_is_unclustered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "article_id": "44444444-4444-4444-8444-444444444444",
                        "title": "Grid",
                        "effective_date": "2026-09-14T12:00:00Z",
                        "distinct_source_count": 1,
                        "story_cluster_id": None,
                        "cluster_source_count": None,
                        "provenance": [
                            {"source_id": WIRE, "source_name": "Wire", "source_country": "US"}
                        ],
                    },
                    "sort": [1],
                }
            ]
        }
    }

    page = await search_articles(
        _Database(),  # type: ignore[arg-type]
        _Session(),  # type: ignore[arg-type]
        Settings(),
        _criteria(),
    )

    assert page.items[0].story_cluster is None


@pytest.mark.asyncio
async def test_story_cluster_filter_requires_a_schema_v3_index() -> None:
    class _V2Target:
        index_name = "articles-v2-test"
        schema_version = 2

    class _V2Database:
        async def scalar(self, _query: object) -> _V2Target:
            return _V2Target()

    with pytest.raises(HTTPException) as excinfo:
        await current_search_target(
            _V2Database(),  # type: ignore[arg-type]
            _criteria(story_cluster_ids=[CLUSTER]),
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "search_upgrade_required"


def _hit(position: int) -> dict[str, Any]:
    return {
        "_source": {
            "article_id": f"44444444-4444-4444-8444-{position:012d}",
            "title": f"Article {position}",
            "effective_date": "2026-09-14T12:00:00Z",
            "distinct_source_count": 1,
            "provenance": [{"source_id": WIRE, "source_name": "Wire", "source_country": "US"}],
        },
        "sort": [position],
    }


async def _page(monkeypatch: pytest.MonkeyPatch, hits: int, limit: int = 2) -> Any:
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.closed, _Adapter.close_error = [], None
    _Adapter.response = {"pit_id": "pit-renewed", "hits": {"hits": [_hit(n) for n in range(hits)]}}
    return await search_articles(
        _Database(),  # type: ignore[arg-type]
        _Session(),  # type: ignore[arg-type]
        Settings(),
        _criteria(),
        limit=limit,
    )


@pytest.mark.asyncio
async def test_search_asks_for_one_hit_beyond_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    await _page(monkeypatch, 0)
    assert _Adapter.body["size"] == 3


@pytest.mark.asyncio
async def test_empty_search_closes_its_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    page = await _page(monkeypatch, 0)
    assert (page.items, page.next_cursor) == ([], None)
    assert _Adapter.closed == ["pit-renewed"]


@pytest.mark.asyncio
async def test_exactly_full_final_page_has_no_cursor_and_closes_its_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = await _page(monkeypatch, 2)
    assert len(page.items) == 2
    assert page.next_cursor is None
    assert _Adapter.closed == ["pit-renewed"]


@pytest.mark.asyncio
async def test_lookahead_hit_is_withheld_and_continues_from_the_last_returned_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = await _page(monkeypatch, 3)
    assert [item.title for item in page.items] == ["Article 0", "Article 1"]
    assert page.next_cursor is not None
    cursor = read_cursor(page.next_cursor, Settings().secret_key)
    assert (cursor["after"], cursor["pit"]) == ([1], "pit-renewed")
    assert _Adapter.closed == []


@pytest.mark.asyncio
async def test_failing_to_close_the_snapshot_still_returns_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    _Adapter.response = {"hits": {"hits": [_hit(0)]}}
    _Adapter.closed, _Adapter.close_error = [], ElasticsearchUnavailable("down")
    page = await search_articles(
        _Database(),  # type: ignore[arg-type]
        _Session(),  # type: ignore[arg-type]
        Settings(),
        _criteria(),
        limit=2,
    )
    assert len(page.items) == 1
    assert _Adapter.closed == ["pit"]


@pytest.mark.parametrize(
    ("schema_version", "minimum", "allowed"),
    [
        (None, 1, True),
        (None, 2, False),
        (1, 1, True),
        (1, 2, False),
        (2, 2, True),
        (2, 3, False),
        (3, 3, True),
    ],
)
@pytest.mark.asyncio
async def test_endpoint_schema_floor(
    schema_version: int | None, minimum: int, allowed: bool
) -> None:
    class _Target:
        index_name = "articles-vx-test"

    _Target.schema_version = schema_version  # type: ignore[attr-defined]

    class _Db:
        async def scalar(self, _query: object) -> object:
            return None if schema_version is None else _Target()

    if allowed:
        index_name, version = await current_search_target(
            _Db(),  # type: ignore[arg-type]
            _criteria(),
            minimum=minimum,
        )
        assert version == (schema_version or 1)
        assert index_name == ("articles-current" if schema_version is None else "articles-vx-test")
    else:
        with pytest.raises(HTTPException) as excinfo:
            await current_search_target(
                _Db(),  # type: ignore[arg-type]
                _criteria(),
                minimum=minimum,
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["code"] == "search_upgrade_required"
