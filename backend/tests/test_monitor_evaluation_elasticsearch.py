import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from test_clustering_postgres import _article, _feed, _membership, _run_clustering

from app.auth.models import User
from app.core.config import get_settings
from app.db.session import session_factory
from app.monitors import evaluation
from app.monitors.evaluation import claim_monitor, process_monitor
from app.monitors.models import Monitor
from app.monitors.schemas import MonitorCreate
from app.monitors.service import create_monitor
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter
from app.search.indexing import process_delivery
from app.search.models import SearchDelivery, SearchIndexTarget
from app.search.service import claim_delivery, request_indexing

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

SETTLE = timedelta(seconds=get_settings().monitor_settle_seconds)


@pytest_asyncio.fixture(loop_scope="session")
async def index_name(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"articles-v3-monitors-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(evaluation, "current_search_target", target)
    return name


async def _index(index: str, article_ids: list[uuid.UUID]) -> None:
    async with session_factory() as db, db.begin():
        target = await db.scalar(
            select(SearchIndexTarget).where(SearchIndexTarget.index_name == index)
        )
        if target is None:
            target = SearchIndexTarget(index_name=index, schema_version=3, role="replacement")
            db.add(target)
            await db.flush()
        target_id = target.id
        for article_id in article_ids:
            await request_indexing(db, article_id)
    async with session_factory() as db:
        delivery_ids = list(
            (
                await db.scalars(
                    select(SearchDelivery.id).where(
                        SearchDelivery.target_id == target_id, SearchDelivery.status == "queued"
                    )
                )
            ).all()
        )
    for delivery_id in delivery_ids:
        async with session_factory() as db:
            claimed = await claim_delivery(db, delivery_id, lease_seconds=60)
        assert claimed is not None
        await process_delivery(str(delivery_id), claimed[1])
    await ElasticsearchAdapter(get_settings().elasticsearch_url).refresh(index)


async def _watch(name: str, kind: str, **state: object) -> uuid.UUID:
    async with session_factory() as db:
        user = User(username=f"watcher-{uuid.uuid4().hex}", password_hash="unused")
        db.add(user)
        await db.flush()
        payload = MonitorCreate.model_validate({"name": name, "kind": kind, "state": state})
        return (await create_monitor(db, user.id, payload)).id


async def _evaluate(monitor_id: uuid.UUID, now: datetime) -> Monitor:
    async with session_factory() as db:
        await db.execute(
            update(Monitor)
            .where(Monitor.id == monitor_id)
            .values(next_evaluation_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
        token = await claim_monitor(db, monitor_id, 60)
    assert token is not None
    assert await process_monitor(monitor_id, token, now=now)
    async with session_factory() as db:
        item = await db.get(Monitor, monitor_id)
        assert item is not None
        return item


async def test_articles_are_counted_exactly_once_across_windows_kinds_and_reruns(
    index_name: str,
) -> None:
    word = f"zorbland{uuid.uuid4().hex[:8]}"
    now0 = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as db, db.begin():
        wire, daily = await _feed(db, "11B Wire"), await _feed(db, "11B Daily")
        seen = await _article(
            db, feeds=[wire], title=f"{word} archive", title_hash=uuid.uuid4().hex,
            discovered=now0 - timedelta(hours=3),
        )  # fmt: skip
        ids = [seen.id]
        feed_id = wire.id
    await _index(index_name, ids)
    by_query = await _watch("By query", "search", q=word)
    by_source = await _watch("By source", "source", source_id=[str(feed_id)])

    for monitor_id in (by_query, by_source):
        baseline = await _evaluate(monitor_id, now0)
        assert (baseline.unseen_article_count, baseline.unseen_cluster_count) == (0, 0)
        assert baseline.latest_match_article_id == seen.id

    async with session_factory() as db, db.begin():
        specs = [
            (wire, f"{word} first", 1),
            (daily, f"{word} second", 10),
            (wire, "unrelated harbour weather", 5),
        ]
        fresh = [
            (
                await _article(
                    db,
                    feeds=[feed],
                    title=title,
                    title_hash=uuid.uuid4().hex,
                    discovered=now0 + timedelta(minutes=minutes),
                )  # fmt: skip
            )
            for feed, title, minutes in specs
        ]
    now1 = now0 + timedelta(hours=1)
    async with session_factory() as db, db.begin():
        inside_lag = await _article(
            db, feeds=[wire], title=f"{word} too fresh", title_hash=uuid.uuid4().hex,
            discovered=now1 - timedelta(seconds=10),
        )  # fmt: skip
    await _index(index_name, [article.id for article in fresh] + [inside_lag.id])

    query_first = await _evaluate(by_query, now1)
    source_first = await _evaluate(by_source, now1)
    assert query_first.unseen_article_count == 2  # first + second; "too fresh" is still settling
    assert source_first.unseen_article_count == 2  # first + the unrelated wire article
    assert query_first.latest_match_article_id == fresh[1].id
    # Nothing here is clustered, so the articles are counted but contribute no stories.
    assert query_first.unseen_cluster_count == 0 and source_first.unseen_cluster_count == 0

    now2 = now1 + timedelta(minutes=10)
    query_second = await _evaluate(by_query, now2)
    source_second = await _evaluate(by_source, now2)
    assert query_second.unseen_article_count == 3 and source_second.unseen_article_count == 3

    async with session_factory() as db:
        await db.execute(
            update(Monitor)
            .where(Monitor.id == by_query)
            .values(eval_cursor_at=query_first.eval_cursor_at)
        )  # a commit lost after the search: the window is evaluated again
        await db.commit()
    rerun = await _evaluate(by_query, now2)
    assert rerun.unseen_article_count == 3 and rerun.eval_cursor_at == now2 - SETTLE


async def test_articles_of_one_story_count_as_one_unseen_cluster(index_name: str) -> None:
    title = f"Harbor council {uuid.uuid4().hex[:8]} votes to fund the dredging programme"
    title_hash = uuid.uuid4().hex
    now0 = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as db, db.begin():
        wire, daily = await _feed(db, "11B Story Wire"), await _feed(db, "11B Story Daily")
        first = await _article(
            db, feeds=[wire], title=title, title_hash=title_hash,
            discovered=now0 + timedelta(minutes=1),
        )  # fmt: skip
        second = await _article(
            db, feeds=[daily], title=title, title_hash=title_hash,
            discovered=now0 + timedelta(minutes=2),
        )  # fmt: skip
        ids = [first.id, second.id]
    await _run_clustering(ids[0])
    await _run_clustering(ids[1])
    cluster_id = next(iter((await _membership(ids)).values()))
    await _index(index_name, ids)

    watching_story = await _watch("Story", "cluster", story_cluster_id=[str(cluster_id)])
    watching_words = await _watch("Words", "search", q=title.split()[2])
    for monitor_id in (watching_story, watching_words):
        await _evaluate(monitor_id, now0 + SETTLE)  # baseline before either article existed
        counted = await _evaluate(monitor_id, now0 + timedelta(hours=1))
        assert (counted.unseen_article_count, counted.unseen_cluster_count) == (2, 1)
