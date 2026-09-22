import os
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from test_compare_postgres import NOW, _get
from test_geo_postgres import _rows, _world
from test_monitor_api_postgres import _client, _user
from test_monitor_evaluation_elasticsearch import _index

from app.core.config import get_settings
from app.geo import investigation
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
ROLES = ("story", "mentioned", "source")


@pytest_asyncio.fixture(loop_scope="session")
async def world(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The geo world's seven articles, alone in a fresh schema 3 index."""
    w = await _world()
    name = f"articles-v3-geo-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object, **_: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(investigation, "current_search_target", target)
    await _index(name, list(vars(w.a).values()))
    return w


async def test_investigation_counts_match_the_exact_recent_map_on_the_same_articles(
    world: Any,
) -> None:
    w = world
    for role in ROLES:
        recent = await _rows(role, w, days=366)  # the whole world, old article included
        found = await _rows(role, w, scope="investigation")
        for code in (w.us, w.gr):
            # Few enough stories and feeds that the cardinality estimate is exact here.
            assert (found[code]["articles"], found[code]["stories"], found[code]["sources"]) == (
                recent[code]["articles"],
                recent[code]["stories"],
                recent[code]["sources"],
            ), (role, code)


async def test_investigation_coverage_counts_documents_and_flags_estimates(world: Any) -> None:
    located = {"story": 5, "mentioned": 2, "source": 5}  # a5/a6 sit on the country-less feed
    for role in ROLES:
        body = await _get("/geo/countries", role=role, scope="investigation")
        assert body["coverage"] == {"unit": "articles", "window_total": 7, "located": located[role]}
        assert (body["scope"], body["days"], body["window_start"], body["window_end"]) == (
            "investigation",
            None,
            None,
            None,
        )
        assert body["stories_estimated"] is True
        assert body["sources_estimated"] is (role == "source")
    recent = await _get("/geo/countries", role="story")
    assert (recent["scope"], recent["stories_estimated"], recent["sources_estimated"]) == (
        "recent",
        False,
        False,
    )


async def test_the_investigation_bounds_are_its_own_dates(world: Any) -> None:
    w = world
    after = (NOW - timedelta(days=39)).date()
    rows = await _rows("story", w, scope="investigation", after=str(after))
    assert rows[w.us]["articles"] == 2  # a1 a4; the 40-day-old article is before the bound
    body = await _get("/geo/countries", role="story", scope="investigation", after=str(after))
    assert body["window_start"].startswith(str(after))
    narrowed = await _rows("story", w, scope="investigation", source_country=w.gr)
    assert (narrowed[w.gr]["articles"], w.us in narrowed) == (1, True)  # a3 on s2; a4 is also s2


async def _walk(client: httpx.AsyncClient, limit: int, **params: Any) -> list[str]:
    ids: list[str] = []
    cursor = None
    for _ in range(20):
        response = await client.get(
            "/api/v1/geo/articles",
            params={
                "scope": "investigation",
                "limit": limit,
                **params,
                **({"cursor": cursor} if cursor else {}),
            },
        )
        assert response.status_code == 200, response.text
        ids += [item["id"] for item in response.json()["items"]]
        cursor = response.json()["next_cursor"]
        if cursor is None:
            return ids
    raise AssertionError("did not terminate")


async def test_investigation_evidence_pages_through_each_role_like_the_recent_map(
    world: Any,
) -> None:
    w = world
    async with _client(await _user()) as client:
        for role in ROLES:
            for code in (w.us, w.gr):
                expected = (await _rows(role, w, scope="investigation"))[code]["articles"]
                full = await _walk(client, 100, role=role, code=code)
                assert len(set(full)) == len(full) == expected, (role, code)
                for size in (1, 2):
                    assert await _walk(client, size, role=role, code=code) == full, (role, size)


async def test_an_investigation_cursor_is_bound_and_released(world: Any) -> None:
    w = world
    async with _client(await _user()) as client:
        params = {"scope": "investigation", "role": "story", "code": w.us, "limit": 2}
        first = (await client.get("/api/v1/geo/articles", params=params)).json()
        cursor = first["next_cursor"]
        assert cursor is not None  # a1, a4 and the old article: three in pages of two
        moved = await client.get(
            "/api/v1/geo/articles", params={**params, "code": w.gr, "cursor": cursor}
        )
        assert moved.status_code == 422
        last = await client.get("/api/v1/geo/articles", params={**params, "cursor": cursor})
        assert last.status_code == 200 and last.json()["next_cursor"] is None
        replay = await client.get("/api/v1/geo/articles", params={**params, "cursor": cursor})
        assert replay.status_code == 409
        assert replay.json()["detail"]["code"] == "restart_search"
    async with _client(await _user()) as stranger:
        other = await stranger.get("/api/v1/geo/articles", params={**params, "cursor": cursor})
        assert other.status_code == 422
