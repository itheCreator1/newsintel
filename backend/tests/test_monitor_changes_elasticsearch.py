import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, update
from test_clustering_postgres import _article, _attach_entities, _entity, _feed
from test_monitor_api_postgres import CSRF, _client
from test_monitor_evaluation_elasticsearch import _evaluate, _index, _watch

from app.clustering.models import StoryCluster, StoryClusterMember
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article, FeedArticle
from app.monitors import changes, evaluation
from app.monitors.changes import cluster_growth
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
    name = f"articles-v3-monitor-changes-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(evaluation, "current_search_target", target)
    monkeypatch.setattr(changes, "current_search_target", target)
    return name


async def _reported(db: Any, article: Article, feed_id: uuid.UUID, at: datetime) -> None:
    await db.execute(
        update(FeedArticle)
        .where(FeedArticle.article_id == article.id, FeedArticle.feed_id == feed_id)
        .values(discovered_at=at)
    )


async def _story(db: Any, members: list[Article], *, cached_sources: int = 99) -> StoryCluster:
    """The cached `source_count` is deliberately wrong: the summary must never read it."""
    cluster = StoryCluster(
        algorithm_version="test",
        article_count=len(members),
        source_count=cached_sources,
        representative_article_id=members[0].id,
    )
    db.add(cluster)
    await db.flush()
    for member in members:
        db.add(
            StoryClusterMember(
                article_id=member.id, cluster_id=cluster.id, score=1.0, algorithm_version="test"
            )
        )
    await db.flush()
    return cluster


async def _owner(monitor_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as db:
        owner = await db.scalar(select(Monitor.user_id).where(Monitor.id == monitor_id))
    assert owner is not None
    return owner


async def _changes(client: httpx.AsyncClient, monitor_id: uuid.UUID) -> dict[str, Any]:
    response = await client.get(f"/api/v1/monitors/{monitor_id}/changes")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_changes_name_new_sources_entities_and_stories_and_repeat_identically(
    index_name: str,
) -> None:
    word = f"kestrel{uuid.uuid4().hex[:8]}"
    now0 = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as db, db.begin():
        wire, daily, herald, tribune = [
            await _feed(db, f"11E {name}") for name in ("Wire", "Daily", "Herald", "Tribune")
        ]
        acme, fresh = await _entity(db, "Acme"), await _entity(db, "Freshco")

        async def make(title: str, feeds: list[Any], minutes: int) -> Article:
            article = await _article(
                db, feeds=feeds, title=title, title_hash=uuid.uuid4().hex,
                discovered=now0 + timedelta(minutes=minutes),
            )  # fmt: skip
            for feed in feeds:
                await _reported(db, article, feed.id, article.first_discovered_at)
            return article

        old = await make(f"{word} archive", [wire], -180)
        one = await make(f"{word} one", [wire, daily], 1)
        two = await make(f"{word} two", [herald], 5)
        three = await make(f"{word} three", [tribune], 10)
        four = await make(f"{word} four", [wire], 12)
        other = await make("unrelated harbour", [wire], 3)
        await _attach_entities(db, old.id, [acme])
        await _attach_entities(db, one.id, [acme])
        await _attach_entities(db, three.id, [fresh])
        grown = await _story(db, [old, one, two])
        started = await _story(db, [three, four])
    everything = [old, one, two, three, four, other]
    await _index(index_name, [article.id for article in everything])
    monitor_id = await _watch("Kestrel", "search", q=word)
    await _evaluate(monitor_id, now0)
    counted = await _evaluate(monitor_id, now0 + timedelta(hours=1))
    assert counted.unseen_article_count == 4

    async with _client(await _owner(monitor_id)) as client:
        body = await _changes(client, monitor_id)
        assert body["article_count"] == 4
        # Wire reported the archive article, so it is not new; the other three sources are.
        assert {s["name"] for s in body["sources"]} == {"11E Daily", "11E Herald", "11E Tribune"}
        assert [(e["name"], e["article_count"]) for e in body["entities"]] == [("Freshco", 1)]
        stories = {s["cluster_id"]: s for s in body["stories"]}
        assert stories[str(grown.id)]["status"] == "grew"
        assert (
            stories[str(grown.id)]["source_count"],
            stories[str(grown.id)]["sources_added"],
        ) == (3, 2)
        assert stories[str(started.id)]["status"] == "new"
        assert stories[str(started.id)]["source_count"] == 2
        assert {e["title"] for s in body["sources"] for e in s["evidence"]} <= {
            f"{word} one", f"{word} two", f"{word} three",
        }  # fmt: skip

        assert await _changes(client, monitor_id) == body  # viewing twice changes nothing

    async with _client(uuid.uuid4()) as stranger:  # a foreign monitor reads as missing
        assert (await stranger.get(f"/api/v1/monitors/{monitor_id}/changes")).status_code == 404

    async with _client(await _owner(monitor_id)) as client:
        viewed = await client.post(
            f"/api/v1/monitors/{monitor_id}/viewed",
            json={"through": body["window_end"]},
            headers=CSRF,
        )
        assert viewed.status_code == 200
        after = await _changes(client, monitor_id)
        assert after["article_count"] == 0
        assert after["sources"] == after["entities"] == after["stories"] == []


async def test_cluster_growth_counts_sources_by_report_time_at_both_boundaries() -> None:
    now0 = datetime.now(UTC).replace(microsecond=0)
    after, upto = now0, now0 + timedelta(hours=1)
    async with session_factory() as db, db.begin():
        a, b, c, d = [await _feed(db, f"11E growth {n}") for n in "abcd"]
        first = await _article(db, feeds=[a, b], title="one", title_hash=uuid.uuid4().hex)
        second = await _article(db, feeds=[c, d], title="two", title_hash=uuid.uuid4().hex)
        await _reported(db, first, a.id, after)  # exactly on the lower bound: already known
        await _reported(
            db, first, b.id, after + timedelta(minutes=1)
        )  # a late report of an old one
        await _reported(db, second, c.id, upto)  # exactly on the upper bound: counted
        await _reported(db, second, d.id, upto + timedelta(seconds=1))  # after the window
        story = await _story(db, [first, second])
        empty = await _story(
            db, [await _article(db, feeds=[], title="x", title_hash=uuid.uuid4().hex)]
        )
    async with session_factory() as db:
        result = await cluster_growth(db, [str(story.id), str(empty.id)], after, upto)

    assert result == {str(story.id): (1, 3)}  # {a} by `after`; {a, b, c} by `upto`; d is later
