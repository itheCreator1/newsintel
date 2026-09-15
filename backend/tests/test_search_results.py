from typing import Any
from uuid import UUID

import pytest

from app.core.config import Settings
from app.search.criteria import SearchCriteria
from app.search.query import parse_query
from app.search.routes import search_articles

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

    def __init__(self, _url: str) -> None:
        pass

    async def open_point_in_time(self, _alias: str) -> str:
        return "pit"

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        _Adapter.body = body
        return _Adapter.response


def _criteria() -> SearchCriteria:
    return SearchCriteria(
        parsed=parse_query(""),
        q="",
        sources=[],
        countries=[],
        start=None,
        end=None,
        content_available=None,
        processing=[],
        languages=[],
        entity_ids=[],
        entity_types=[],
        keyword_ids=[],
        story_countries=[],
        mentioned_countries=[],
    )


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
