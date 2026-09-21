import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app import scheduler
from app.auth.models import User
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article
from app.investigations.schemas import InvestigationState
from app.jobs import monitors as monitor_jobs
from app.monitors import evaluation
from app.monitors import routes as monitor_routes
from app.monitors.evaluation import claim_monitor, process_monitor
from app.monitors.models import Monitor
from app.monitors.schemas import MonitorCreate, MonitorUpdate
from app.monitors.service import create_monitor, update_monitor
from app.search.elasticsearch import ElasticsearchUnavailable

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

SETTINGS = get_settings()
SETTLE = timedelta(seconds=SETTINGS.monitor_settle_seconds)


class FakeAdapter:
    """Answers each search from a queue and can run a hook first (to race the evaluation)."""

    def __init__(self, *responses: Any, hook: Any = None) -> None:
        self.responses = list(responses)
        self.hook = hook

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.hook:
            await self.hook()
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[no-any-return]


def _response(
    total: int, clusters: int | None = None, article_id: uuid.UUID | None = None
) -> dict[str, Any]:
    hits = (
        [
            {
                "_source": {
                    "article_id": str(article_id or uuid.uuid4()),
                    "first_discovered_at": datetime.now(UTC).isoformat(),
                }
            }
        ]
        if total
        else []
    )
    body: dict[str, Any] = {"hits": {"total": {"value": total}, "hits": hits}}
    if clusters is not None:
        body["aggregations"] = {"clusters": {"value": clusters}}
    return body


async def _monitor(name: str = "Grid") -> uuid.UUID:
    async with session_factory() as db:
        user = User(username=f"watcher-{uuid.uuid4().hex}", password_hash="unused")
        db.add(user)
        await db.flush()
        item = await create_monitor(
            db, user.id, MonitorCreate(name=name, kind="search", state=InvestigationState(q="grid"))
        )
        return item.id


async def _read(monitor_id: uuid.UUID) -> Monitor:
    async with session_factory() as db:
        item = await db.get(Monitor, monitor_id)
        assert item is not None
        return item


async def _set(monitor_id: uuid.UUID, **values: Any) -> None:
    async with session_factory() as db:
        item = await db.get(Monitor, monitor_id)
        assert item is not None
        for key, value in values.items():
            setattr(item, key, value)
        await db.commit()


async def _claim(monitor_id: uuid.UUID) -> str:
    await _set(monitor_id, next_evaluation_at=datetime.now(UTC) - timedelta(seconds=1))
    async with session_factory() as db:
        token = await claim_monitor(db, monitor_id, 60)
    assert token is not None
    return token


async def _baselined() -> tuple[uuid.UUID, datetime]:
    monitor_id = await _monitor()
    now = datetime.now(UTC)
    assert await process_monitor(
        monitor_id, await _claim(monitor_id), FakeAdapter(_response(0)), now=now
    )
    return monitor_id, now - SETTLE


async def test_only_due_enabled_unleased_monitors_can_be_claimed() -> None:
    monitor_id = await _monitor()

    async with session_factory() as db:
        token = await claim_monitor(db, monitor_id, 60)
        assert token is not None
        assert await claim_monitor(db, monitor_id, 60) is None
    claimed = await _read(monitor_id)
    assert claimed.claim_token == token and claimed.claim_expires_at is not None
    assert claimed.claim_expires_at > datetime.now(UTC)

    await _set(monitor_id, claim_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    async with session_factory() as db:
        assert await claim_monitor(db, monitor_id, 60) not in (None, token)

    await _set(monitor_id, claim_token=None, claim_expires_at=None, enabled=False)
    async with session_factory() as db:
        assert await claim_monitor(db, monitor_id, 60) is None
    await _set(monitor_id, enabled=True, next_evaluation_at=datetime.now(UTC) + timedelta(hours=1))
    async with session_factory() as db:
        assert await claim_monitor(db, monitor_id, 60) is None
        item = await db.get(Monitor, monitor_id)
        assert item is not None
        await update_monitor(db, item, MonitorUpdate(enabled=False))
        await update_monitor(db, item, MonitorUpdate(enabled=True))
        assert await claim_monitor(db, monitor_id, 60) is not None


async def test_the_scheduler_claims_due_monitors_and_leaves_failed_enqueues_to_the_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued, broken = await _monitor("Queued"), await _monitor("Broken")
    disabled = await _monitor("Off")
    await _set(disabled, enabled=False)
    sent: list[tuple[str, str]] = []

    def send(monitor_id: str, token: str) -> None:
        if monitor_id == str(broken):
            raise RuntimeError("redis down")
        sent.append((monitor_id, token))

    monkeypatch.setattr(monitor_jobs.evaluate_monitor, "send", send)
    await scheduler.schedule_due_monitors(batch_size=500)

    assert str(queued) in {monitor_id for monitor_id, _ in sent}
    assert str(disabled) not in {monitor_id for monitor_id, _ in sent}
    assert (await _read(queued)).claim_token == dict(sent)[str(queued)]
    stranded = await _read(broken)
    assert stranded.claim_expires_at is not None and stranded.claim_expires_at > datetime.now(UTC)


async def test_first_run_baselines_then_counts_are_set_from_the_unseen_window() -> None:
    monitor_id = await _monitor()
    now = datetime.now(UTC)
    horizon = now - SETTLE

    assert await process_monitor(
        monitor_id, await _claim(monitor_id), FakeAdapter(_response(12)), now=now
    )
    baseline = await _read(monitor_id)
    assert (baseline.eval_cursor_at, baseline.viewed_cursor_at) == (horizon, horizon)
    assert (baseline.unseen_article_count, baseline.unseen_cluster_count) == (0, 0)
    assert baseline.claim_token is None and baseline.last_evaluated_at == now
    assert baseline.next_evaluation_at == now + timedelta(seconds=SETTINGS.monitor_interval_seconds)

    later = now + timedelta(minutes=10)
    article_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Article(
                id=article_id,
                original_url=f"https://example.test/{article_id}",
                normalized_url=f"https://example.test/{article_id}",
                title="Grid",
                normalized_title_hash=uuid.uuid4().hex,
                first_discovered_at=later,
            )
        )
        await db.commit()
    adapter = FakeAdapter(_response(3), _response(7, clusters=2, article_id=article_id))
    assert await process_monitor(monitor_id, await _claim(monitor_id), adapter, now=later)
    counted = await _read(monitor_id)
    assert (counted.unseen_article_count, counted.unseen_cluster_count) == (7, 2)
    assert (
        counted.eval_cursor_at == later - SETTLE and counted.latest_match_article_id == article_id
    )
    assert counted.viewed_cursor_at == horizon


async def test_rerunning_a_window_after_a_lost_commit_yields_the_same_counts() -> None:
    monitor_id, baseline = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)
    results = []
    for _ in range(2):
        adapter = FakeAdapter(_response(3), _response(7, clusters=2))
        assert await process_monitor(monitor_id, await _claim(monitor_id), adapter, now=now)
        item = await _read(monitor_id)
        results.append((item.unseen_article_count, item.unseen_cluster_count, item.eval_cursor_at))
        await _set(monitor_id, eval_cursor_at=baseline)  # as if the commit never happened

    assert results[0] == results[1] == (7, 2, now - SETTLE)

    empty = FakeAdapter(_response(0))
    assert await process_monitor(
        monitor_id, await _claim(monitor_id), empty, now=now + timedelta(minutes=1)
    )
    after = await _read(monitor_id)
    assert after.unseen_article_count == 7 and after.unseen_cluster_count == 2


async def test_concurrent_delivery_with_one_token_publishes_once() -> None:
    monitor_id, _ = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)
    token = await _claim(monitor_id)

    outcomes = await asyncio.gather(
        *(
            process_monitor(
                monitor_id, token, FakeAdapter(_response(3), _response(4, clusters=1)), now=now
            )
            for _ in range(2)
        )
    )

    assert sorted(outcomes) == [False, True]
    item = await _read(monitor_id)
    assert (item.unseen_article_count, item.unseen_cluster_count) == (4, 1)


async def test_a_wrong_token_or_expired_lease_changes_nothing() -> None:
    monitor_id, baseline = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)
    token = await _claim(monitor_id)

    assert not await process_monitor(monitor_id, "other", FakeAdapter(_response(0)), now=now)
    await _set(monitor_id, claim_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert not await process_monitor(monitor_id, token, FakeAdapter(_response(0)), now=now)

    item = await _read(monitor_id)
    assert item.eval_cursor_at == baseline and item.last_evaluated_at != now


async def test_an_edited_target_or_a_new_view_during_evaluation_discards_the_result() -> None:
    monitor_id, baseline = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)

    async def edit_target() -> None:
        async with session_factory() as db:
            item = await db.get(Monitor, monitor_id)
            assert item is not None
            await update_monitor(
                db, item, MonitorUpdate(kind="search", state=InvestigationState(q="gas"))
            )

    adapter = FakeAdapter(_response(3), _response(9, clusters=1), hook=edit_target)
    assert not await process_monitor(monitor_id, await _claim(monitor_id), adapter, now=now)
    edited = await _read(monitor_id)
    assert edited.eval_cursor_at is None and edited.unseen_article_count == 0

    monitor_id, baseline = await _baselined()

    async def view() -> None:
        await _set(monitor_id, viewed_cursor_at=baseline + timedelta(minutes=5))

    adapter = FakeAdapter(_response(3), _response(9, clusters=1), hook=view)
    assert not await process_monitor(monitor_id, await _claim(monitor_id), adapter, now=now)
    viewed = await _read(monitor_id)
    assert viewed.eval_cursor_at == baseline and viewed.unseen_article_count == 0


async def test_a_criteria_edit_clears_what_a_concurrent_evaluation_is_writing() -> None:
    monitor_id, baseline = await _baselined()
    owner = (await _read(monitor_id)).user_id
    async with session_factory() as writer:
        # The evaluation holds the row and writes a cursor it has not committed yet.
        item = await writer.get(Monitor, monitor_id, with_for_update=True)
        assert item is not None
        item.eval_cursor_at = baseline + timedelta(minutes=5)
        item.claim_token = "running"
        await writer.flush()

        async def edit() -> None:
            async with session_factory() as db:
                await monitor_routes.update(
                    monitor_id,
                    MonitorUpdate(kind="search", state=InvestigationState(q="gas")),
                    db,
                    SimpleNamespace(user_id=owner),  # type: ignore[arg-type]
                )

        editing = asyncio.create_task(edit())
        await asyncio.sleep(0.3)
        await writer.commit()
        await editing
    edited = await _read(monitor_id)
    assert edited.eval_cursor_at is None and edited.claim_token is None


async def test_a_match_missing_from_postgres_does_not_break_the_commit() -> None:
    monitor_id, _ = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)

    adapter = FakeAdapter(_response(1), _response(5, clusters=1))
    assert await process_monitor(monitor_id, await _claim(monitor_id), adapter, now=now)

    item = await _read(monitor_id)
    assert item.unseen_article_count == 5 and item.latest_match_article_id is None


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (ElasticsearchUnavailable("connection refused"), "search_unavailable"),
        (RuntimeError("boom"), "evaluation_error"),
    ],
)
async def test_failures_are_recorded_retried_later_and_cleared_by_success(
    failure: Exception, category: str
) -> None:
    monitor_id, baseline = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)

    assert not await process_monitor(
        monitor_id, await _claim(monitor_id), FakeAdapter(failure), now=now
    )
    failed = await _read(monitor_id)
    assert failed.error_category == category and failed.error_message
    assert failed.claim_token is None and failed.eval_cursor_at == baseline
    assert failed.next_evaluation_at == now + timedelta(seconds=SETTINGS.monitor_retry_seconds)

    assert await process_monitor(
        monitor_id, await _claim(monitor_id), FakeAdapter(_response(0)), now=now
    )
    recovered = await _read(monitor_id)
    assert recovered.error_category is None and recovered.error_message is None


async def test_an_index_that_needs_a_rebuild_and_unreadable_state_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor_id, _ = await _baselined()
    now = datetime.now(UTC) + timedelta(minutes=10)

    async def needs_rebuild(db: object, criteria: object) -> tuple[str, int]:
        raise HTTPException(409, {"code": "search_upgrade_required", "message": "Rebuild search"})

    monkeypatch.setattr(evaluation, "current_search_target", needs_rebuild)
    assert not await process_monitor(monitor_id, await _claim(monitor_id), FakeAdapter(), now=now)
    assert (await _read(monitor_id)).error_category == "search_upgrade_required"
    monkeypatch.undo()

    await _set(monitor_id, state={"retired_filter": True})
    assert not await process_monitor(monitor_id, await _claim(monitor_id), FakeAdapter(), now=now)
    unreadable = await _read(monitor_id)
    assert unreadable.error_category == "invalid_state" and unreadable.error_message
