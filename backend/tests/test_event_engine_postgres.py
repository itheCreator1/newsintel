import asyncio
import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from event_fixtures import BASE, annotate, entities, event_of, story
from sqlalchemy import delete, func, select, text

from app.clustering.models import StoryCluster, StoryClusterMember
from app.db.session import session_factory
from app.events import engine
from app.events.engine import (
    EVENT_ADVISORY_LOCK,
    RuleEventAssociator,
    candidate_event_ids,
    reconcile_events,
)
from app.events.models import Event, EventCluster, EventEntity
from app.events.service import create_event, set_entities
from app.nlp.models import (
    ArticleEntity,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_slate():  # type: ignore[no-untyped-def]
    """Clusters anywhere in the shared database are dirty for a new version, so start empty."""
    async with session_factory() as db, db.begin():
        await db.execute(delete(Event))
        await db.execute(delete(StoryCluster))
    yield


def associator() -> RuleEventAssociator:
    """A version of its own, so tests never see each other's events."""
    item = RuleEventAssociator()
    item.version = f"t-{uuid.uuid4().hex[:12]}"
    return item


async def run(item: RuleEventAssociator, limit: int = 100) -> engine.BatchResult:
    async with session_factory() as db, db.begin():
        return await item.run_batch(db, limit)


async def test_a_new_cluster_becomes_an_event_with_derived_fields() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 3)
        cluster = await story(db, ents, country="GR", hours=2)
        cluster_id = cluster.id
    result = await run(item)
    assert (result.evaluated, result.created, result.failed) == (1, 1, 0)
    async with session_factory() as db:
        event = await event_of(db, cluster_id, item.version)
        assert event is not None and event.algorithm_version == item.version
        assert event.status == "active" and event.primary_country == "GR"
        assert event.started_at == event.ended_at == BASE + timedelta(hours=2)
        counts = dict(
            (
                await db.execute(
                    select(EventEntity.entity_id, EventEntity.article_count).where(
                        EventEntity.event_id == event.id
                    )
                )
            ).all()
        )
        assert counts == {e.id: 2 for e in ents}  # distinct articles, not clusters
        row = await db.scalar(select(EventCluster).where(EventCluster.cluster_id == cluster_id))
        cluster = await db.get(StoryCluster, cluster_id)
        assert row is not None and cluster is not None
        assert (
            row.cluster_updated_at == cluster.updated_at and row.algorithm_version == item.version
        )


async def test_related_clusters_share_an_event_and_unrelated_ones_do_not() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 4)
        first = await story(db, ents[:3], country="GR")
        related = await story(db, ents[:3], hours=6, country="GR")
        other_entities = await story(db, [ents[3], *await entities(db, 2)], hours=1)
        too_late = await story(db, ents[:3], hours=24 * 10, country="GR")
        ids = [first.id, related.id, other_entities.id, too_late.id]
    result = await run(item)
    assert (result.evaluated, result.created) == (4, 3)
    async with session_factory() as db:
        events = [await event_of(db, i, item.version) for i in ids]
        assert all(events)
        assert events[0].id == events[1].id  # type: ignore[union-attr]
        assert len({e.id for e in events}) == 3  # type: ignore[union-attr]
        joined = events[0]
        assert joined is not None
        assert (joined.started_at, joined.ended_at) == (BASE, BASE + timedelta(hours=6))
        signals = await db.scalar(
            select(EventCluster.signals).where(EventCluster.cluster_id == related.id)
        )
        assert signals is not None and signals["entities"] == 1.0 and signals["location"] == 1.0


async def test_a_rerun_writes_nothing() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 3)
        await story(db, ents)
        await story(db, ents, hours=1)
    await run(item)
    async with session_factory() as db:
        before = (await db.execute(select(Event.id, Event.updated_at).order_by(Event.id))).all()
        rows = (
            await db.execute(
                select(EventCluster.cluster_id, EventCluster.cluster_updated_at).order_by(
                    EventCluster.cluster_id
                )
            )
        ).all()
    again = await run(item)
    assert (again.evaluated, again.created, again.failed) == (0, 0, 0)
    async with session_factory() as db, db.begin():
        swept = await reconcile_events(db, item.version)
        assert swept.deleted == 0
    async with session_factory() as db:
        assert (
            before
            == (await db.execute(select(Event.id, Event.updated_at).order_by(Event.id))).all()
        )
        assert (
            rows
            == (
                await db.execute(
                    select(EventCluster.cluster_id, EventCluster.cluster_updated_at).order_by(
                        EventCluster.cluster_id
                    )
                )
            ).all()
        )


async def test_a_changed_cluster_is_decided_again() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 4)
        cluster = await story(db, ents[:3])
        cluster_id = cluster.id
    await run(item)
    async with session_factory() as db, db.begin():
        cluster = await db.get(StoryCluster, cluster_id)
        assert cluster is not None
        member = await db.scalar(
            select(StoryClusterMember).where(StoryClusterMember.cluster_id == cluster_id)
        )
        assert member is not None
        await annotate(db, member.article_id, [ents[3]])
        cluster.article_count += 1  # what `_refresh_cluster` does: bumps `updated_at`
    result = await run(item)
    assert result.evaluated == 1 and result.created == 0
    async with session_factory() as db:
        event = await event_of(db, cluster_id, item.version)
        assert event is not None
        found = set(
            await db.scalars(select(EventEntity.entity_id).where(EventEntity.event_id == event.id))
        )
        assert found == {e.id for e in ents}
    assert (await run(item)).evaluated == 0


async def test_a_cluster_that_no_longer_fits_moves_and_its_old_event_is_refreshed() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 6)
        stays = await story(db, ents[:3], hours=0)
        leaves = await story(db, ents[:3], hours=3)
        stays_id, leaves_id = stays.id, leaves.id
    await run(item)
    async with session_factory() as db:
        assert (await event_of(db, stays_id, item.version)).id == (  # type: ignore[union-attr]
            await event_of(db, leaves_id, item.version)
        ).id  # type: ignore[union-attr]
    async with session_factory() as db, db.begin():
        cluster = await db.get(StoryCluster, leaves_id)
        assert cluster is not None
        members = list(
            await db.scalars(
                select(StoryClusterMember.article_id).where(
                    StoryClusterMember.cluster_id == leaves_id
                )
            )
        )
        await db.execute(delete(ArticleEntity).where(ArticleEntity.article_id.in_(members)))
        for article_id in members:
            await annotate(db, article_id, ents[3:])
        cluster.article_count += 1
    result = await run(item)
    assert (result.evaluated, result.created) == (1, 1)
    async with session_factory() as db:
        old, new = (
            await event_of(db, stays_id, item.version),
            await event_of(db, leaves_id, item.version),
        )
        assert old is not None and new is not None and old.id != new.id
        assert old.ended_at == BASE  # the old event lost the later cluster's span
        old_entities = set(
            await db.scalars(select(EventEntity.entity_id).where(EventEntity.event_id == old.id))
        )
        assert old_entities == {e.id for e in ents[:3]}


async def test_a_cluster_is_not_kept_in_its_event_by_its_own_evidence() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 6)
        stays = await story(db, ents[:3], country="GR")
        leaves = await story(db, ents[:3], title="ferry strike port", country="GR")
        stays_id, leaves_id = stays.id, leaves.id
    await run(item)
    async with session_factory() as db, db.begin():
        assert (await event_of(db, stays_id, item.version)).id == (  # type: ignore[union-attr]
            await event_of(db, leaves_id, item.version)
        ).id  # type: ignore[union-attr]
        cluster = await db.get(StoryCluster, leaves_id)
        assert cluster is not None
        members = list(
            await db.scalars(
                select(StoryClusterMember.article_id).where(
                    StoryClusterMember.cluster_id == leaves_id
                )
            )
        )
        await db.execute(delete(ArticleEntity).where(ArticleEntity.article_id.in_(members)))
        for article_id in members:
            await annotate(db, article_id, ents[3:])
        cluster.article_count += 1
    # Against the other member alone: same time and country (0.40), nothing else shared. Its own
    # headline would add 0.20 and keep it at the 0.50 threshold.
    result = await run(item)
    assert (result.evaluated, result.created) == (1, 1)
    async with session_factory() as db:
        assert (await event_of(db, stays_id, item.version)).id != (  # type: ignore[union-attr]
            await event_of(db, leaves_id, item.version)
        ).id  # type: ignore[union-attr]


async def test_a_single_cluster_event_keeps_its_id_when_the_cluster_changes() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 4)
        cluster_id = (await story(db, ents[:3])).id
    await run(item)
    async with session_factory() as db, db.begin():
        before = await event_of(db, cluster_id, item.version)
        assert before is not None
        cluster = await db.get(StoryCluster, cluster_id)
        assert cluster is not None
        cluster.article_count += 1
    result = await run(item)
    assert (result.evaluated, result.created, result.deleted) == (1, 0, 0)
    async with session_factory() as db:
        assert (await event_of(db, cluster_id, item.version)).id == before.id  # type: ignore[union-attr]
    assert (await run(item)).evaluated == 0  # decided, so no longer dirty


async def test_merged_or_dissolved_clusters_refresh_and_finally_delete_their_event() -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 3)
        early = await story(db, ents, hours=0)
        late = await story(db, ents, hours=5)
        early_id, late_id = early.id, late.id
    await run(item)
    async with session_factory() as db:
        event = await event_of(db, early_id, item.version)
        assert event is not None
        event_id = event.id
        assert event.ended_at == BASE + timedelta(hours=5)

    async with session_factory() as db, db.begin():  # clustering dissolves the later cluster
        await db.execute(delete(StoryCluster).where(StoryCluster.id == late_id))
    async with session_factory() as db, db.begin():
        assert (await reconcile_events(db, item.version)).deleted == 0
    async with session_factory() as db:
        event = await db.get(Event, event_id)
        assert event is not None and event.ended_at == BASE

    async with session_factory() as db, db.begin():
        await db.execute(delete(StoryCluster).where(StoryCluster.id == early_id))
    async with session_factory() as db, db.begin():
        assert (await reconcile_events(db, item.version)).deleted == 1
    async with session_factory() as db:
        assert await db.get(Event, event_id) is None
        assert await db.scalar(select(func.count()).select_from(EventEntity)) == 0


async def test_a_failing_cluster_is_skipped_and_retried_without_blocking_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = associator()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 3)
        bad = await story(db, ents, hours=0)
        good = await story(db, await entities(db, 3), hours=1)
        bad_id, good_id = bad.id, good.id
    real = engine.choose_event

    def broken(cluster, *args, **kwargs):  # type: ignore[no-untyped-def]
        if cluster.id == bad_id:
            raise RuntimeError("boom")
        return real(cluster, *args, **kwargs)

    monkeypatch.setattr(engine, "choose_event", broken)
    result = await run(item)
    assert (result.evaluated, result.failed, result.created) == (1, 1, 1)
    async with session_factory() as db:
        assert await event_of(db, good_id, item.version) is not None
        assert await event_of(db, bad_id, item.version) is None
        assert await db.scalar(select(func.count()).select_from(Event)) == 1  # nothing half-made
    monkeypatch.setattr(engine, "choose_event", real)
    assert (await run(item)).evaluated == 1
    async with session_factory() as db:
        assert await event_of(db, bad_id, item.version) is not None


async def test_versions_coexist_and_a_held_lock_skips_the_run() -> None:
    old, new = associator(), associator()
    async with session_factory() as db, db.begin():
        await story(db, await entities(db, 3))
    await run(old)
    await run(new)
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(EventCluster)) == 2
        versions = set(await db.scalars(select(Event.algorithm_version)))
        assert versions == {old.version, new.version}
    assert (await run(old)).evaluated == 0  # the newer version did not disturb the older

    async with session_factory() as holder, holder.begin():
        await holder.execute(text(f"SELECT pg_advisory_xact_lock({EVENT_ADVISORY_LOCK})"))
        assert (await run(old)).skipped is True


async def test_candidates_are_bounded_recent_and_index_backed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = f"t-{uuid.uuid4().hex[:12]}"
    async with session_factory() as db, db.begin():
        ents = await entities(db, 3)
        made = []
        for days in range(4):  # four events sharing the entities, ending on different days
            event = await create_event(db, version)
            event.started_at = event.ended_at = BASE + timedelta(days=days)
            await set_entities(db, event, {e.id: 1 for e in ents})
            made.append(event.id)
        far = await create_event(db, version)
        far.started_at = far.ended_at = BASE + timedelta(days=60)
        await set_entities(db, far, {e.id: 1 for e in ents})
        cluster = engine.ClusterFacts(
            uuid.uuid4(),
            BASE + timedelta(days=1),
            BASE + timedelta(days=2),
            frozenset(),
            frozenset(e.id for e in ents),
            None,
        )
        monkeypatch.setattr(engine, "EVENT_CANDIDATE_LIMIT", 2)
        found = await candidate_event_ids(db, cluster, version)
        assert found == [made[3], made[2]]  # newest first, capped; the far event is out of window
        one_entity = engine.ClusterFacts(
            cluster.id, cluster.first_published_at, cluster.last_published_at,
            frozenset(), frozenset([ents[0].id]), None,
        )  # fmt: skip
        assert await candidate_event_ids(db, one_entity, version) == []  # needs two shared

        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in await db.execute(
                text(
                    "EXPLAIN SELECT e.id FROM events e JOIN event_entities ee ON ee.event_id = e.id"
                    " WHERE ee.entity_id = :entity AND e.status = 'active' AND e.ended_at >= :since"
                ),
                {"entity": ents[0].id, "since": BASE},
            )
        )
        assert "ix_event_entities_entity" in plan or "event_entities_pkey" in plan, plan
        assert "ix_events_status_time" in plan or "events_pkey" in plan, plan
        await db.rollback()


async def test_downgrade_to_0011_drops_only_the_basis_column() -> None:
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0011")
    try:
        async with session_factory() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns"
                        " WHERE table_name = 'event_clusters'"
                        " AND column_name = 'cluster_updated_at'"
                    )
                )
                == 0
            )
            assert await db.scalar(text("SELECT to_regclass('event_clusters') IS NOT NULL"))
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with session_factory() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'event_clusters' AND column_name = 'cluster_updated_at'"
                )
            )
            == 1
        )
