import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from test_phase7_postgres import _article, _feed
from test_phase11b_elasticsearch import _evaluate, _index, _watch
from test_phase11c_postgres import CSRF, _client

from app.core.config import get_settings
from app.db.session import session_factory
from app.monitors import evaluation, results
from app.monitors.models import Monitor
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
    name = f"articles-v3-monitor-api-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(evaluation, "current_search_target", target)
    monkeypatch.setattr(results, "current_search_target", target)
    return name


async def _owner(monitor_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as db:
        owner = await db.scalar(select(Monitor.user_id).where(Monitor.id == monitor_id))
    assert owner is not None
    return owner


async def _page(client: httpx.AsyncClient, monitor_id: uuid.UUID, **params: Any) -> dict[str, Any]:
    response = await client.get(f"/api/v1/monitors/{monitor_id}/results", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _all(client: httpx.AsyncClient, monitor_id: uuid.UUID, **params: Any) -> list[str]:
    ids: list[str] = []
    cursor = None
    while True:
        page = await _page(
            client, monitor_id, limit=1, **params, **({"cursor": cursor} if cursor else {})
        )
        ids += [item["article_id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            return ids


async def test_results_are_the_counted_articles_and_viewing_never_hides_unseen_ones(
    index_name: str,
) -> None:
    word = f"vantar{uuid.uuid4().hex[:8]}"
    now0 = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "11C Wire")
        old = await _article(
            db, feeds=[wire], title=f"{word} archive", title_hash=uuid.uuid4().hex,
            discovered=now0 - timedelta(hours=3),
        )  # fmt: skip
        specs = [(f"{word} one", 1), (f"{word} two", 10), (f"{word} three", 20), ("harbour", 5)]
        fresh = [
            await _article(
                db,
                feeds=[wire],
                title=title,
                title_hash=uuid.uuid4().hex,
                discovered=now0 + timedelta(minutes=minutes),
            )  # fmt: skip
            for title, minutes in specs
        ]
    await _index(index_name, [old.id] + [article.id for article in fresh])
    monitor_id = await _watch("Vantar", "search", q=word)
    await _evaluate(monitor_id, now0)
    now1 = now0 + timedelta(hours=1)
    counted = await _evaluate(monitor_id, now1)
    assert counted.unseen_article_count == 3
    expected = [str(fresh[2].id), str(fresh[1].id), str(fresh[0].id)]  # newest first

    async with _client(await _owner(monitor_id)) as client:
        first = await _page(client, monitor_id)
        assert [item["article_id"] for item in first["items"]] == expected
        assert first["window_end"] == counted.eval_cursor_at.isoformat().replace("+00:00", "Z")

        # Later evaluations move the cursor mid-scroll; the page chain keeps its own window.
        page_one = await _page(client, monitor_id, limit=1)
        await _evaluate(monitor_id, now1 + timedelta(minutes=10))
        chain = [page_one["items"][0]["article_id"]]
        cursor = page_one["next_cursor"]
        while cursor:
            page = await _page(client, monitor_id, limit=1, cursor=cursor)
            chain += [item["article_id"] for item in page["items"]]
            cursor = page["next_cursor"]
        assert chain == expected
        assert (
            await client.get(f"/api/v1/monitors/{monitor_id}/results", params={"cursor": "x"})
        ).status_code == 422

        recent = await _all(client, monitor_id, scope="recent")
        assert recent == expected + [str(old.id)]

        viewed = await client.post(
            f"/api/v1/monitors/{monitor_id}/viewed",
            json={"through": first["window_end"]},
            headers=CSRF,
        )
        assert viewed.status_code == 200
        assert (await _page(client, monitor_id))["items"] == []
        assert await _all(client, monitor_id, scope="recent") == expected + [str(old.id)]


async def test_a_boundary_the_evaluator_has_passed_is_recounted_rather_than_lost(
    index_name: str,
) -> None:
    word = f"vantar{uuid.uuid4().hex[:8]}"
    now0 = datetime.now(UTC).replace(microsecond=0)
    now1, now2 = now0 + timedelta(hours=1), now0 + timedelta(hours=1, minutes=10)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "11C Late Wire")
        first = await _article(
            db, feeds=[wire], title=f"{word} first", title_hash=uuid.uuid4().hex,
            discovered=now0 + timedelta(minutes=1),
        )  # fmt: skip
        late = await _article(
            db, feeds=[wire], title=f"{word} late", title_hash=uuid.uuid4().hex,
            discovered=now1 + timedelta(minutes=5),
        )  # fmt: skip
    await _index(index_name, [first.id, late.id])
    monitor_id = await _watch("Late", "search", q=word)
    await _evaluate(monitor_id, now0)
    seen = await _evaluate(monitor_id, now1)
    assert seen.unseen_article_count == 1
    async with _client(await _owner(monitor_id)) as client:
        boundary = (await _page(client, monitor_id))["window_end"]
        advanced = await _evaluate(monitor_id, now2)
        assert advanced.unseen_article_count == 2  # the analyst has only seen the first article

        viewed = await client.post(
            f"/api/v1/monitors/{monitor_id}/viewed", json={"through": boundary}, headers=CSRF
        )
        assert viewed.status_code == 200 and viewed.json()["unseen_article_count"] == 0

        recounted = await _evaluate(monitor_id, now2)
        assert recounted.unseen_article_count == 1
        assert [item["article_id"] for item in (await _page(client, monitor_id))["items"]] == [
            str(late.id)
        ]
