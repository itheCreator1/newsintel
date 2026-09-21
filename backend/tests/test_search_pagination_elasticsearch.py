import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from test_clustering_postgres import _article, _feed
from test_monitor_api_postgres import _client, _user
from test_monitor_evaluation_elasticsearch import _index

from app.core.config import get_settings
from app.db.session import session_factory
from app.search import routes
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def index_name(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"articles-v3-pagination-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object, **_: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(routes, "current_search_target", target)
    return name


async def _search(client: httpx.AsyncClient, **params: Any) -> httpx.Response:
    return await client.get("/api/v1/search", params={"sort": "newest", "limit": 2, **params})


async def test_pages_end_exactly_at_the_last_hit_and_release_the_snapshot(
    index_name: str,
) -> None:
    word = f"paginar{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "15A Wire")
        articles = [
            await _article(
                db,
                feeds=[wire],
                title=f"{word} {n}",
                title_hash=uuid.uuid4().hex,
                discovered=now + timedelta(minutes=n),
            )
            for n in range(4)
        ]
    await _index(index_name, [article.id for article in articles])

    async with _client(await _user()) as client:
        first = await _search(client, q=word)
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert cursor is not None
        second = await _search(client, q=word, cursor=cursor)
        assert second.status_code == 200, second.text
        ids = [item["article_id"] for item in first.json()["items"] + second.json()["items"]]
        assert sorted(ids) == sorted(str(article.id) for article in articles)
        # Four hits in pages of two: the second page is the last, with no empty page after it.
        assert second.json()["next_cursor"] is None

        # The final page closed its snapshot, so replaying its cursor asks for a restart.
        replay = await _search(client, q=word, cursor=cursor)
        assert replay.status_code == 409, replay.text
        assert replay.json()["detail"]["code"] == "restart_search"

        empty = await _search(client, q=f"{word}absent")
        assert empty.status_code == 200
        assert empty.json() == {"items": [], "next_cursor": None}
