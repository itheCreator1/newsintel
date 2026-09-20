import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.clustering.models import StoryCluster
from app.db.session import session_factory
from app.events.models import Event, EventCluster, EventEntity
from app.events.service import (
    associate_cluster,
    create_event,
    event_for_cluster,
    get_event,
    refresh_span,
    set_entities,
    set_status,
)
from app.feeds.models import Article
from app.nlp.models import Entity

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)


async def _cluster(db, first=BASE, last=BASE) -> StoryCluster:  # type: ignore[no-untyped-def]
    cluster = StoryCluster(
        algorithm_version="rule-1", first_published_at=first, last_published_at=last
    )
    db.add(cluster)
    await db.flush()
    return cluster


async def _entity(db) -> Entity:  # type: ignore[no-untyped-def]
    name = uuid.uuid4().hex
    entity = Entity(language="en", entity_type="ORG", normalized_text=name, display_text=name)
    db.add(entity)
    await db.flush()
    return entity


async def _rejects(db, *rows) -> None:  # type: ignore[no-untyped-def]
    db.add_all(rows)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()
    db.expunge_all()


async def test_create_applies_defaults_and_updated_at_moves() -> None:
    async with session_factory() as db:
        event = await create_event(db, "event-1")
        await db.commit()
        assert event.status == "active" and event.algorithm_version == "event-1"
        assert event.started_at is None and event.ended_at is None and event.primary_country is None
        assert event.created_at is not None
        before = event.updated_at
        await set_status(db, event, "closed")
        await db.commit()
        await db.refresh(event)
        assert event.status == "closed" and event.updated_at >= before
        with pytest.raises(ValueError):
            await set_status(db, event, "merged")
        assert (await get_event(db, event.id)) is not None
        assert await get_event(db, uuid.uuid4()) is None


async def test_database_rejects_invalid_events() -> None:
    async with session_factory() as db:
        await _rejects(db, Event(algorithm_version="event-1", status="bogus"))
        await _rejects(
            db,
            Event(algorithm_version="event-1", started_at=BASE, ended_at=BASE - timedelta(hours=1)),
        )
        db.add(Event(algorithm_version="event-1", started_at=BASE))  # one open end is allowed
        await db.flush()
        await db.rollback()


async def test_a_cluster_joins_one_event_per_version() -> None:
    async with session_factory() as db:
        cluster = await _cluster(db)
        first = await create_event(db, "event-1")
        other = await create_event(db, "event-1")
        newer = await create_event(db, "event-2")
        await db.commit()
        cluster_id, first_id, other_id, newer_id = cluster.id, first.id, other.id, newer.id
        for event_id, version in ((first_id, "event-1"), (newer_id, "event-2")):
            db.add(
                EventCluster(
                    event_id=event_id, cluster_id=cluster_id, algorithm_version=version, score=1
                )
            )
        await db.commit()  # a newer version may associate the same cluster again

        duplicate = EventCluster(
            event_id=other_id, cluster_id=cluster_id, algorithm_version="event-1", score=1
        )
        await _rejects(db, duplicate)
        found = await event_for_cluster(db, cluster_id, "event-1")
        assert found is not None and found.id == first_id
        found = await event_for_cluster(db, cluster_id, "event-2")
        assert found is not None and found.id == newer_id
        assert await event_for_cluster(db, cluster_id, "event-3") is None


async def test_an_association_cannot_disagree_with_its_events_version() -> None:
    async with session_factory() as db:
        cluster = await _cluster(db)
        event = await create_event(db, "event-1")
        await db.commit()
        await _rejects(
            db,
            EventCluster(
                event_id=event.id, cluster_id=cluster.id, algorithm_version="event-2", score=1
            ),
        )


async def test_associations_hold_many_clusters_and_are_idempotent_and_moving() -> None:
    async with session_factory() as db:
        one, two = await _cluster(db), await _cluster(db)
        event, other = await create_event(db, "event-1"), await create_event(db, "event-1")
        await associate_cluster(db, event, one.id, 0.5, {"shared_entities": 2})
        await associate_cluster(db, event, two.id, 0.7)
        again = await associate_cluster(db, event, one.id, 0.9, {"shared_entities": 3})
        assert (again.score, again.signals) == (0.9, {"shared_entities": 3})
        moved = await associate_cluster(db, other, one.id, 0.4)
        await db.commit()
        assert moved.event_id == other.id
        rows = await db.scalars(
            select(EventCluster.cluster_id).where(EventCluster.event_id == event.id)
        )
        assert list(rows) == [two.id]
        default = await db.scalar(
            select(EventCluster.signals).where(EventCluster.cluster_id == two.id)
        )
        assert default == {}


async def test_entities_are_replaced_and_counted() -> None:
    async with session_factory() as db:
        event = await create_event(db, "event-1")
        keep, drop, add = await _entity(db), await _entity(db), await _entity(db)
        await set_entities(db, event, {keep.id: 2, drop.id: 1})
        await set_entities(db, event, {keep.id: 5, add.id: 3})
        await db.commit()
        rows = await db.execute(
            select(EventEntity.entity_id, EventEntity.article_count).where(
                EventEntity.event_id == event.id
            )
        )
        assert dict(rows.all()) == {keep.id: 5, add.id: 3}
        await _rejects(db, EventEntity(event_id=event.id, entity_id=keep.id, article_count=0))


async def test_refresh_span_follows_the_member_clusters() -> None:
    async with session_factory() as db:
        early = await _cluster(db, BASE, BASE + timedelta(hours=1))
        late = await _cluster(db, BASE + timedelta(days=1), BASE + timedelta(days=2))
        event = await create_event(db, "event-1")
        await refresh_span(db, event)
        assert event.started_at is None and event.ended_at is None
        await associate_cluster(db, event, early.id, 1)
        await associate_cluster(db, event, late.id, 1)
        await refresh_span(db, event)
        assert (event.started_at, event.ended_at) == (BASE, BASE + timedelta(days=2))
        await db.rollback()


async def test_deletes_cascade_to_associations_only() -> None:
    async with session_factory() as db:
        cluster, gone_cluster = await _cluster(db), await _cluster(db)
        entity, gone_entity = await _entity(db), await _entity(db)
        event = await create_event(db, "event-1")
        await associate_cluster(db, event, cluster.id, 1)
        await associate_cluster(db, event, gone_cluster.id, 1)
        await set_entities(db, event, {entity.id: 1, gone_entity.id: 1})
        await db.commit()
        event_id, cluster_id, entity_id = event.id, cluster.id, entity.id

        await db.delete(gone_cluster)
        await db.delete(gone_entity)
        await db.commit()
        db.expunge_all()
        assert (
            await db.scalar(
                select(func.count())
                .select_from(EventCluster)
                .where(EventCluster.event_id == event_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(EventEntity)
                .where(EventEntity.event_id == event_id)
            )
            == 1
        )
        assert await db.get(Event, event_id) is not None

        await db.delete(await db.get(Event, event_id))
        await db.commit()
        db.expunge_all()
        assert (
            await db.scalar(
                select(func.count())
                .select_from(EventCluster)
                .where(EventCluster.event_id == event_id)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(EventEntity)
                .where(EventEntity.event_id == event_id)
            )
            == 0
        )
        assert await db.get(StoryCluster, cluster_id) is not None
        assert await db.get(Entity, entity_id) is not None


async def test_lookups_use_their_indexes() -> None:
    queries = {
        "ix_events_status_time": "SELECT id FROM events WHERE status = 'active'"
        " ORDER BY ended_at DESC, id DESC LIMIT 10",
        "ix_events_country_time": "SELECT id FROM events WHERE primary_country = 'GR'"
        " ORDER BY ended_at DESC, id DESC LIMIT 10",
        "ix_event_clusters_cluster": "SELECT event_id FROM event_clusters WHERE cluster_id = :id",
        "ix_event_entities_entity": "SELECT event_id FROM event_entities WHERE entity_id = :id",
    }
    async with session_factory() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        for index, query in queries.items():
            plan = "\n".join(
                row[0] for row in await db.execute(text(f"EXPLAIN {query}"), {"id": uuid.uuid4()})
            )
            assert index in plan, plan
        await db.rollback()


async def test_deleting_an_article_leaves_events_alone() -> None:
    async with session_factory() as db:
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Grid",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=BASE,
            first_discovered_at=BASE,
        )
        db.add(article)
        await db.flush()
        cluster = await _cluster(db)
        cluster.representative_article_id = article.id
        event = await create_event(db, "event-1")
        await associate_cluster(db, event, cluster.id, 1)
        await db.commit()
        await db.delete(article)
        await db.commit()
        db.expunge_all()
        assert await event_for_cluster(db, cluster.id, "event-1") is not None


async def test_downgrade_to_0010_removes_only_the_event_tables() -> None:
    async with session_factory() as db:
        clusters_before = await db.scalar(select(func.count()).select_from(StoryCluster))

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0010")
    try:
        async with session_factory() as db:
            for table in ("events", "event_clusters", "event_entities"):
                assert await db.scalar(text(f"SELECT to_regclass('{table}') IS NULL")) is True
            assert await db.scalar(text("SELECT to_regclass('monitors') IS NOT NULL")) is True
            assert (
                await db.scalar(select(func.count()).select_from(StoryCluster)) == clusters_before
            )
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

    async with session_factory() as db:
        assert await db.scalar(text("SELECT to_regclass('event_clusters') IS NOT NULL")) is True
