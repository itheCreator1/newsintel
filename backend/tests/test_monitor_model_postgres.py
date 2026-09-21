import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth.models import User
from app.db.session import session_factory
from app.feeds.models import Article
from app.investigations.models import SavedSearch
from app.investigations.schemas import STATE_VERSION, InvestigationState
from app.monitors.models import Monitor
from app.monitors.schemas import MonitorCreate, MonitorUpdate
from app.monitors.service import (
    InvalidMonitorCursor,
    MonitorNameConflict,
    create_monitor,
    get_monitor,
    list_monitors,
    update_monitor,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)
HISTORY = (
    "eval_cursor_at",
    "eval_cursor_article_id",
    "viewed_cursor_at",
    "viewed_cursor_article_id",
    "latest_match_at",
    "latest_match_article_id",
    "last_evaluated_at",
    "claim_token",
    "claim_expires_at",
    "error_category",
    "error_message",
)


async def _user(db) -> User:  # type: ignore[no-untyped-def]
    user = User(username=f"watcher-{uuid.uuid4().hex}", password_hash="unused")
    db.add(user)
    await db.flush()
    return user


def _create(name: str = "Grid", q: str = "grid") -> MonitorCreate:
    return MonitorCreate(name=name, kind="search", state=InvestigationState(q=q))


async def _article(db) -> Article:  # type: ignore[no-untyped-def]
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
    return article


async def test_create_applies_defaults_and_stores_a_state_snapshot() -> None:
    async with session_factory() as db:
        user = await _user(db)
        monitor = await create_monitor(db, user.id, _create())

        assert monitor.enabled is True
        assert (monitor.unseen_article_count, monitor.unseen_cluster_count) == (0, 0)
        assert monitor.state_version == STATE_VERSION
        assert monitor.state["q"] == "grid" and monitor.kind == "search"
        assert monitor.next_evaluation_at is not None
        assert monitor.created_at is not None and monitor.updated_at is not None
        assert all(getattr(monitor, field) is None for field in HISTORY)


async def test_database_rejects_invalid_rows() -> None:
    async with session_factory() as db:
        user_id = (await _user(db)).id
        await db.commit()
        bad_rows = {
            "blank name": {"name": "   "},
            "unknown kind": {"kind": "topic"},
            "negative article counter": {"unseen_article_count": -1},
            "negative cluster counter": {"unseen_cluster_count": -1},
            "zero state version": {"state_version": 0},
        }
        for label, override in bad_rows.items():
            db.add(
                Monitor(
                    user_id=user_id,
                    **{
                        "name": "Valid",
                        "kind": "search",
                        "state_version": STATE_VERSION,
                        "state": {"q": "grid"},
                        **override,
                    },
                )
            )
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()
            db.expunge_all()
            assert label


async def test_names_are_unique_per_user_ignoring_case_but_not_across_users() -> None:
    async with session_factory() as db:
        first_id, second_id = (await _user(db)).id, (await _user(db)).id
        await create_monitor(db, first_id, _create("Grid"))

        with pytest.raises(MonitorNameConflict):
            await create_monitor(db, first_id, _create("grid"))
        assert (await create_monitor(db, second_id, _create("GRID"))).user_id == second_id


async def test_get_and_list_are_owner_scoped_and_keyset_paginated() -> None:
    async with session_factory() as db:
        owner, other = await _user(db), await _user(db)
        made = [await create_monitor(db, owner.id, _create(name)) for name in ("b", "a", "c")]
        foreign = await create_monitor(db, other.id, _create("a"))

        assert await get_monitor(db, other.id, made[0].id) is None
        assert await get_monitor(db, owner.id, made[0].id) is not None
        assert await get_monitor(db, owner.id, foreign.id) is None

        page, cursor = await list_monitors(db, owner.id, None, 2)
        assert [item.name for item in page] == ["a", "b"] and cursor is not None
        rest, end = await list_monitors(db, owner.id, cursor, 2)
        assert [item.name for item in rest] == ["c"] and end is None
        with pytest.raises(InvalidMonitorCursor):
            await list_monitors(db, owner.id, "bogus", 2)


async def test_rename_and_toggle_keep_history_but_a_new_target_resets_it() -> None:
    async with session_factory() as db:
        user = await _user(db)
        article = await _article(db)
        monitor = await create_monitor(db, user.id, _create())
        monitor.eval_cursor_at = monitor.viewed_cursor_at = BASE
        monitor.latest_match_at = monitor.last_evaluated_at = BASE
        monitor.eval_cursor_article_id = article.id
        monitor.viewed_cursor_article_id = monitor.latest_match_article_id = article.id
        monitor.claim_token, monitor.claim_expires_at = "held", BASE
        monitor.unseen_article_count, monitor.unseen_cluster_count = 4, 2
        monitor.error_category, monitor.error_message = "search_unavailable", "down"
        await db.commit()

        renamed = await update_monitor(db, monitor, MonitorUpdate(name="Power", enabled=False))
        assert (renamed.name, renamed.enabled) == ("Power", False)
        assert renamed.eval_cursor_at == BASE and renamed.unseen_article_count == 4

        same = await update_monitor(
            db, renamed, MonitorUpdate(kind="search", state=InvestigationState(q="grid"))
        )
        assert same.unseen_article_count == 4 and same.eval_cursor_article_id == article.id

        moved = await update_monitor(
            db, same, MonitorUpdate(kind="search", state=InvestigationState(q="gas"))
        )
        assert moved.state["q"] == "gas"
        assert all(getattr(moved, field) is None for field in HISTORY)
        assert (moved.unseen_article_count, moved.unseen_cluster_count) == (0, 0)
        assert moved.enabled is False


async def test_reenabling_schedules_an_evaluation_now_and_a_conflicting_rename_is_reported() -> (
    None
):
    async with session_factory() as db:
        user = await _user(db)
        monitor = await create_monitor(db, user.id, _create("Grid"))
        await create_monitor(db, user.id, _create("Gas", q="gas"))
        monitor.enabled = False
        monitor.next_evaluation_at = BASE
        await db.commit()

        enabled = await update_monitor(db, monitor, MonitorUpdate(enabled=True))
        assert enabled.enabled is True
        assert enabled.next_evaluation_at > datetime.now(UTC) - timedelta(minutes=1)

        with pytest.raises(MonitorNameConflict):
            await update_monitor(db, enabled, MonitorUpdate(name="gas"))


async def test_deleting_the_user_or_the_matched_article_is_handled_by_the_database() -> None:
    async with session_factory() as db:
        user = await _user(db)
        article = await _article(db)
        monitor = await create_monitor(db, user.id, _create())
        monitor.latest_match_article_id = article.id
        await db.commit()

        await db.delete(article)
        await db.commit()
        await db.refresh(monitor)
        assert monitor.latest_match_article_id is None

        monitor_id = monitor.id
        await db.delete(user)
        await db.commit()
        db.expunge_all()
        assert await db.get(Monitor, monitor_id) is None


async def test_due_lookup_uses_the_partial_index_and_skips_disabled_monitors() -> None:
    horizon = {"now": BASE + timedelta(days=1)}
    query = (
        "SELECT id FROM monitors WHERE enabled AND next_evaluation_at <= :now"
        " ORDER BY next_evaluation_at, id LIMIT 10"
    )
    async with session_factory() as db:
        user = await _user(db)
        for index in range(3):
            monitor = await create_monitor(db, user.id, _create(f"Due {index}", q=f"due {index}"))
            monitor.enabled = index != 1
            monitor.next_evaluation_at = BASE + timedelta(minutes=index)
        await db.commit()

        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in await db.execute(text(f"EXPLAIN {query}"), horizon))
        assert "ix_monitors_due" in plan, plan
        due = await db.scalars(
            select(Monitor.name).where(
                Monitor.user_id == user.id,
                Monitor.enabled,
                Monitor.next_evaluation_at <= horizon["now"],
            )
        )
        assert sorted(due) == ["Due 0", "Due 2"]
        await db.rollback()


async def test_claim_tokens_are_unique() -> None:
    async with session_factory() as db:
        user = await _user(db)
        first = await create_monitor(db, user.id, _create("One"))
        second = await create_monitor(db, user.id, _create("Two", q="two"))
        first.claim_token = second.claim_token = "shared-token"
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


async def test_downgrade_to_0009_removes_only_monitors() -> None:
    async with session_factory() as db:
        articles_before = await db.scalar(select(func.count()).select_from(Article))
        searches_before = await db.scalar(select(func.count()).select_from(SavedSearch))

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0009")
    try:
        async with session_factory() as db:
            assert await db.scalar(text("SELECT to_regclass('monitors') IS NULL")) is True
            assert await db.scalar(select(func.count()).select_from(Article)) == articles_before
            assert await db.scalar(select(func.count()).select_from(SavedSearch)) == searches_before
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

    async with session_factory() as db:
        assert await db.scalar(text("SELECT to_regclass('monitors') IS NOT NULL")) is True
