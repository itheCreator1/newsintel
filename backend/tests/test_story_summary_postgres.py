"""The story page's header: its title, entities, event, timeline and who reported it first."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from event_fixtures import annotate, entities, feed
from fastapi import HTTPException

from app.clustering.models import StoryCluster, StoryClusterMember
from app.clustering.routes import get_cluster_summary
from app.db.session import session_factory
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event, EventCluster
from app.feeds.models import Article, Feed, FeedArticle

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

START = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)


async def _member(db, cluster: StoryCluster, source: Feed, title: str, at: datetime) -> Article:  # type: ignore[no-untyped-def]
    article = Article(
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title=title,
        normalized_title_hash=uuid.uuid4().hex,
        published_at=at,
        first_discovered_at=at + timedelta(minutes=5),
    )
    db.add(article)
    await db.flush()
    db.add(
        StoryClusterMember(
            article_id=article.id, cluster_id=cluster.id, score=1, algorithm_version="rule-1"
        )
    )
    db.add(
        FeedArticle(
            feed_id=source.id,
            article_id=article.id,
            guid=uuid.uuid4().hex,
            feed_title=title,
            feed_url=article.original_url,
            metadata_json={},
        )
    )
    await db.flush()
    return article


async def _cluster(db) -> StoryCluster:  # type: ignore[no-untyped-def]
    cluster = StoryCluster(algorithm_version="rule-1", article_count=0, source_count=0)
    db.add(cluster)
    await db.flush()
    return cluster


async def test_a_story_is_named_by_its_representative_and_says_who_reported_it_first() -> None:
    async with session_factory() as db, db.begin():
        first, second, third = await feed(db), await feed(db), await feed(db)
        main, side, noise = await entities(db, 3)
        main.display_text, side.display_text, noise.hidden = "Harbour Authority", "Piraeus", True
        cluster = await _cluster(db)
        opener = await _member(db, cluster, first, "Fire at the harbour", START)
        lead = await _member(
            db, cluster, second, "Harbour fire spreads", START + timedelta(hours=2)
        )
        late = await _member(
            db, cluster, third, "Fire spreads", START + timedelta(hours=2, minutes=30)
        )
        follow = await _member(db, cluster, third, "Fire out", START + timedelta(hours=5))
        await annotate(db, opener.id, [main, noise])
        await annotate(db, lead.id, [main, side])
        await annotate(db, late.id, [main, side])
        await annotate(db, follow.id, [main])
        cluster.representative_article_id = lead.id
        event = Event(algorithm_version=EVENT_ALGORITHM_VERSION, status="active")
        db.add(event)
        await db.flush()
        db.add(
            EventCluster(
                event_id=event.id,
                cluster_id=cluster.id,
                algorithm_version=EVENT_ALGORITHM_VERSION,
                score=1,
            )
        )
        await db.flush()
        cluster_id, event_id = cluster.id, event.id
        names = {first.id: first.name, second.id: second.name, third.id: third.name}
        sources = [first.id, second.id, third.id]
        main_id, side_id = main.id, side.id

    async with session_factory() as db:
        summary = await get_cluster_summary(cluster_id, db, None)  # type: ignore[arg-type]

    assert summary.title == "Harbour fire spreads"
    # Most articles first; a name hidden as not a name is left out.
    assert [(item.id, item.display_name, item.article_count) for item in summary.entities] == [
        (main_id, "Harbour Authority", 4),
        (side_id, "Piraeus", 2),
    ]
    assert summary.event is not None
    assert summary.event.id == event_id and summary.event.headline == "Harbour fire spreads"
    # Hour buckets with the quiet hours in between, so the chart keeps its time axis.
    assert summary.timeline.interval == "hour"
    assert [(bucket.start, bucket.count) for bucket in summary.timeline.buckets] == [
        (START + timedelta(hours=hour), count)
        for hour, count in [(0, 1), (1, 0), (2, 2), (3, 0), (4, 0), (5, 1)]
    ]
    # Each source once, at its first article, behind the story's first report.
    assert [
        (item.id, item.name, item.first_reported_at, item.minutes_behind)
        for item in summary.sources
    ] == [
        (sources[0], names[sources[0]], START, 0.0),
        (sources[1], names[sources[1]], START + timedelta(hours=2), 120.0),
        (sources[2], names[sources[2]], START + timedelta(hours=2, minutes=30), 150.0),
    ]


async def test_a_long_story_is_charted_by_day_and_a_story_without_an_event_says_so() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        cluster = await _cluster(db)
        await _member(db, cluster, source, "Day one", START)
        await _member(db, cluster, source, "Day nine", START + timedelta(days=8, hours=3))
        cluster_id = cluster.id

    async with session_factory() as db:
        summary = await get_cluster_summary(cluster_id, db, None)  # type: ignore[arg-type]

    # No representative recorded: the earliest article names the story.
    assert summary.title == "Day one"
    assert summary.event is None and summary.entities == []
    assert summary.timeline.interval == "day"
    day = START.replace(hour=0)
    buckets = [(bucket.start, bucket.count) for bucket in summary.timeline.buckets]
    assert buckets[0] == (day, 1) and buckets[-1] == (day + timedelta(days=8), 1)
    assert len(buckets) == 9 and sum(count for _, count in buckets) == 2
    assert [item.minutes_behind for item in summary.sources] == [0.0]


async def test_an_unknown_story_is_not_found() -> None:
    async with session_factory() as db:
        with pytest.raises(HTTPException) as excinfo:
            await get_cluster_summary(uuid.uuid4(), db, None)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 404
