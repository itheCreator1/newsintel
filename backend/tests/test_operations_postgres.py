import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from event_fixtures import DIGEST, feed
from sqlalchemy import delete, func, select
from sqlalchemy import event as sa_event
from sqlalchemy.engine import Engine
from test_compare_postgres import _article, _get, _status

from app.auth.models import User
from app.clustering.models import ArticleClusterState, ClusterJob
from app.core.config import get_settings
from app.db.session import session_factory
from app.events import execution
from app.events.engine import EVENT_ALGORITHM_VERSION, BatchResult, dirty_clusters
from app.events.models import EventAssociationRun
from app.feeds.models import ArticleProcessingJob, Feed, FeedFetch
from app.monitors.models import Monitor
from app.nlp import execution as nlp_execution
from app.nlp.models import ArticleNlpState, NlpJob, NlpProcessorRun
from app.nlp.processors import ConfigurationError
from app.operations import queries
from app.search.models import SearchDelivery, SearchIndexTarget

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
NOW = datetime.now(UTC)
HOURS = 24
_DIRTY = select(func.count()).select_from(dirty_clusters(EVENT_ALGORITHM_VERSION).subquery())
INSIDE = NOW - timedelta(hours=2)
OUTSIDE = NOW - timedelta(hours=30)
ANCIENT = NOW - timedelta(days=400)
FIELDS = ("queued", "running", "retrying", "failed", "lease_expired")
WINDOWED = ("completed_in_window", "failed_in_window")

# The tests add rows inside one transaction, read the operations numbers from it, and roll back,
# so they never depend on what other tests left in the shared database: they assert deltas.


# -- job rows -----------------------------------------------------------------------------------


async def _add_jobs(db, kind: str) -> None:  # type: ignore[no-untyped-def]
    """The same scenario for every pipeline: 1 queued (due long ago), 1 running with a live lease
    and 1 with an expired one, 1 retrying that is not due yet, 1 failed and 1 succeeded inside the
    window, and 1 of each outside it."""
    source = await feed(db)
    plan = [
        ("queued", ANCIENT, None, None),
        ("running", INSIDE, NOW + timedelta(minutes=5), None),
        ("running", INSIDE, NOW - timedelta(minutes=5), None),
        ("retrying", NOW + timedelta(hours=1), None, None),
        ("failed", INSIDE, None, INSIDE),
        ("succeeded", INSIDE, None, INSIDE),
        ("failed", OUTSIDE, None, OUTSIDE),
        ("succeeded", OUTSIDE, None, OUTSIDE),
    ]
    target = None
    if kind == "search":
        target = SearchIndexTarget(
            index_name=f"idx-{uuid.uuid4().hex}", schema_version=1, role="retained"
        )
        db.add(target)
        await db.flush()
    for status, due, lease, done in plan:
        article = await _article(db, source, INSIDE)
        finished = done if status in ("failed", "succeeded") else None
        if kind == "article":
            db.add(
                ArticleProcessingJob(
                    article_id=article.id, requested_mode="rss", stage="fetch", status=status,
                    next_attempt_at=due, claim_expires_at=lease, completed_at=finished,
                )
            )  # fmt: skip
        elif kind == "search":
            db.add(
                SearchDelivery(
                    article_id=article.id, target_id=target.id, requested_revision=1,
                    status=status, next_attempt_at=due, claim_expires_at=lease,
                    updated_at=finished or INSIDE,
                )
            )  # fmt: skip
        elif kind == "nlp":
            state = ArticleNlpState(
                article_id=article.id, processor_name="entities", input_fingerprint=DIGEST,
                processor_version="t", configuration_fingerprint=DIGEST,
            )  # fmt: skip
            db.add(state)
            await db.flush()
            job = NlpJob(
                state_id=state.id, article_id=article.id, processor_name="entities", generation=1,
                input_fingerprint=DIGEST, processor_version="t",
                configuration_fingerprint=DIGEST, status=status, next_attempt_at=due,
                claim_expires_at=lease,
            )  # fmt: skip
            db.add(job)
            await db.flush()
            if (
                finished
            ):  # the window counts finished processor runs, as failed jobs have no end time
                db.add(
                    NlpProcessorRun(
                        job_id=job.id, article_id=article.id, processor_name="entities",
                        processor_version="t", algorithm_version="t",
                        configuration_fingerprint=DIGEST,
                        input_fingerprint=DIGEST, generation=1,
                        outcome="failed" if status == "failed" else "success",
                        completed_at=finished,
                    )
                )  # fmt: skip
        else:
            state = ArticleClusterState(article_id=article.id, algorithm_version="rule-1")
            db.add(state)
            await db.flush()
            db.add(
                ClusterJob(
                    state_id=state.id, article_id=article.id, generation=1,
                    algorithm_version="rule-1", status=status, next_attempt_at=due,
                    claim_expires_at=lease, completed_at=finished,
                )
            )  # fmt: skip
    await db.flush()


def _job_pipeline(body, kind: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return next(p for p in body.model_dump()["jobs"] if p["key"] == kind)


@pytest.mark.parametrize("kind", ["article", "search", "nlp", "clustering"])
async def test_a_job_pipeline_counts_states_lease_expiry_and_the_window(kind: str) -> None:
    async with session_factory() as db:
        before = _job_pipeline(await queries.pipelines(db, NOW, HOURS), kind)
        await _add_jobs(db, kind)
        after = _job_pipeline(await queries.pipelines(db, NOW, HOURS), kind)
        await db.rollback()
    delta = {f: after[f] - before[f] for f in (*FIELDS, *WINDOWED)}
    assert delta == {
        "queued": 1, "running": 2, "retrying": 1, "failed": 2, "lease_expired": 1,
        "completed_in_window": 1, "failed_in_window": 1,
    }  # fmt: skip
    # The queued item was due 400 days ago; the retrying one is not due yet, so it is not waiting.
    assert after["oldest_wait_seconds"] >= 400 * 86400
    assert after["oldest_wait_seconds"] < 401 * 86400
    assert after["definition"] and after["window_basis"]


async def test_the_window_edge_is_inclusive_and_a_narrower_window_excludes() -> None:
    async with session_factory() as db:
        source = await feed(db)
        article = await _article(db, source, INSIDE)
        edge = NOW - timedelta(hours=HOURS)
        db.add(
            ArticleProcessingJob(
                article_id=article.id, requested_mode="rss", stage="fetch", status="succeeded",
                completed_at=edge,
            )
        )  # fmt: skip
        await db.flush()
        wide = _job_pipeline(await queries.pipelines(db, NOW, HOURS), "article")
        just_after = _job_pipeline(
            await queries.pipelines(db, NOW + timedelta(seconds=1), HOURS), "article"
        )
        await db.rollback()
    assert wide["completed_in_window"] - just_after["completed_in_window"] == 1


# -- events and monitors ------------------------------------------------------------------------


async def test_events_report_the_dirty_backlog_the_engine_would_process() -> None:
    from test_compare_postgres import _cluster

    async with session_factory() as db:
        engine_dirty = (await db.execute(_DIRTY)).scalar_one()
        source = await feed(db)
        made = [await _article(db, source, INSIDE) for _ in range(4)]
        await _cluster(db, made[:2], 1)
        await _cluster(db, made[2:], 1)
        await db.flush()
        shown = (await queries.pipelines(db, NOW, HOURS)).events
        counted = (await db.execute(_DIRTY)).scalar_one()
        await db.rollback()
    assert counted == engine_dirty + 2
    assert shown.dirty_clusters == counted
    assert shown.algorithm_version == EVENT_ALGORITHM_VERSION


async def test_monitors_are_counted_across_users_without_their_names_or_queries() -> None:
    async with session_factory() as db:
        before = (await queries.pipelines(db, NOW, HOURS)).monitors
        users = [User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="x") for _ in range(2)]
        db.add_all(users)
        await db.flush()

        def make(user: User, name: str, **kw: Any) -> Monitor:
            return Monitor(
                user_id=user.id, name=name, kind="search", state_version=1, state={"q": name}, **kw
            )

        db.add_all(
            [
                make(users[0], "secret-due", next_evaluation_at=NOW - timedelta(hours=3)),
                make(users[1], "other-due", next_evaluation_at=NOW - timedelta(minutes=1)),
                make(users[0], "later", next_evaluation_at=NOW + timedelta(hours=1)),
                make(users[1], "paused", enabled=False, next_evaluation_at=NOW - timedelta(days=1)),
                make(users[1], "broken", error_category="search_unavailable",
                     next_evaluation_at=NOW + timedelta(hours=1)),
            ]
        )  # fmt: skip
        await db.flush()
        result = await queries.pipelines(db, NOW, HOURS)
        after = result.monitors
        await db.rollback()
    assert after.total - before.total == 5
    assert after.enabled - before.enabled == 4  # a disabled monitor is not "due", however late
    assert after.due - before.due == 2
    assert after.in_error - before.in_error == 1
    assert after.oldest_overdue_seconds >= 3 * 3600
    categories = {c.category: c.count for c in after.by_error_category}
    assert (
        categories["search_unavailable"]
        - {c.category: c.count for c in before.by_error_category}.get("search_unavailable", 0)
        == 1
    )
    assert "secret-due" not in result.model_dump_json()


# -- event association runs ---------------------------------------------------------------------


class _Fake:
    version = EVENT_ALGORITHM_VERSION

    def __init__(self, *results: BatchResult | Exception) -> None:
        self.results = list(results)

    async def run_batch(self, db, limit):  # type: ignore[no-untyped-def]
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def _runs_since(start: datetime) -> list[EventAssociationRun]:
    async with session_factory() as db:
        return list(
            await db.scalars(
                select(EventAssociationRun)
                .where(EventAssociationRun.started_at >= start)
                .order_by(EventAssociationRun.started_at)
            )
        )


async def _forget(start: datetime) -> None:
    async with session_factory() as db, db.begin():
        await db.execute(delete(EventAssociationRun).where(EventAssociationRun.started_at >= start))


async def test_a_run_that_did_work_or_failed_is_recorded_and_an_idle_one_is_not(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    start = datetime.now(UTC)
    try:
        monkeypatch.setattr(execution, "associator", lambda: _Fake(BatchResult()))
        await execution.process_events()  # idle tick
        assert await _runs_since(start) == []

        monkeypatch.setattr(
            execution, "associator", lambda: _Fake(BatchResult(evaluated=3, created=1, deleted=0))
        )
        await execution.process_events(sweep=True)
        monkeypatch.setattr(
            execution,
            "associator",
            lambda: _Fake(BatchResult(evaluated=1, failed=2, last_error="ValueError: bad cluster")),
        )
        await execution.process_events()
        monkeypatch.setattr(execution, "associator", lambda: _Fake(RuntimeError("db went away")))
        await execution.process_events()

        work, partial, crashed = await _runs_since(start)
        assert (work.evaluated, work.created, work.failed, work.sweep) == (3, 1, 0, True)
        assert work.error_category is None
        assert (partial.evaluated, partial.failed) == (1, 2)
        assert partial.error_category == "event_association"
        assert partial.error_message == "ValueError: bad cluster"
        assert crashed.error_category == "event_association"
        assert "RuntimeError" in (crashed.error_message or "")
        assert all(r.completed_at >= r.started_at >= start for r in (work, partial, crashed))
    finally:
        await _forget(start)


async def test_run_rows_older_than_the_retention_are_deleted_as_new_ones_are_written(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    start = datetime.now(UTC)
    old = start - execution.RUN_RETENTION - timedelta(days=1)
    try:
        async with session_factory() as db, db.begin():
            db.add(EventAssociationRun(started_at=old, completed_at=old, evaluated=1))
        monkeypatch.setattr(execution, "associator", lambda: _Fake(BatchResult(evaluated=1)))
        await execution.process_events()
        async with session_factory() as db:
            remaining = await db.scalar(
                select(func.count()).select_from(EventAssociationRun).where(
                    EventAssociationRun.started_at < start - execution.RUN_RETENTION
                )
            )  # fmt: skip
        assert remaining == 0
    finally:
        await _forget(start)


async def test_the_events_pipeline_reports_the_runs_in_the_window() -> None:
    async with session_factory() as db:
        before = (await queries.pipelines(db, NOW, HOURS)).events

        def run(ago: timedelta, **kw: Any) -> EventAssociationRun:
            return EventAssociationRun(started_at=NOW - ago, completed_at=NOW - ago, **kw)

        db.add_all(
            [
                run(timedelta(hours=1), evaluated=4),
                run(timedelta(minutes=10), evaluated=1, failed=2,
                    error_category="event_association", error_message="Boom: x"),
                run(timedelta(hours=40), evaluated=1, failed=1, error_category="event_association"),
            ]
        )  # fmt: skip
        await db.flush()
        after = (await queries.pipelines(db, NOW, HOURS)).events
        await db.rollback()
    assert after.runs_in_window - before.runs_in_window == 2
    assert after.failed_clusters_in_window - before.failed_clusters_in_window == 2
    assert after.last_run_at is not None and after.last_run_at >= NOW - timedelta(minutes=10)
    assert after.last_error is not None and after.last_error.message == "Boom: x"
    assert after.last_success_at is not None  # the run an hour ago had no failure


# -- feeds --------------------------------------------------------------------------------------


async def _fetches(db, item: Feed, *pattern: str, start: datetime = INSIDE) -> None:  # type: ignore[no-untyped-def]
    """Oldest first: S success (10 entries, 2 invalid, 3 new), F failed (http_transient), Q queued."""
    for i, mark in enumerate(pattern):
        at = start - timedelta(minutes=len(pattern) - i)
        db.add(
            FeedFetch(
                feed_id=item.id, claim_token=uuid.uuid4().hex, started_at=at, completed_at=at,
                status={"S": "success", "F": "failed", "Q": "queued"}[mark],
                entry_count=10 if mark == "S" else 0, invalid_entry_count=2 if mark == "S" else 0,
                new_article_count=3 if mark == "S" else 0,
                error_category="http_transient" if mark == "F" else None,
            )
        )  # fmt: skip
    await db.flush()


async def _make_feed(db, **kw: Any) -> Feed:  # type: ignore[no-untyped-def]
    item = await feed(db)
    item.poll_interval_minutes = 30
    item.next_poll_at = NOW + timedelta(minutes=10)
    for key, value in kw.items():
        setattr(item, key, value)
    await db.flush()
    return item


async def test_feed_state_streaks_and_totals() -> None:
    async with session_factory() as db:
        before = await queries.feeds(db, NOW, HOURS)
        ok = await _make_feed(db, last_success_at=INSIDE)
        await _fetches(db, ok, "F", "S")
        failing = await _make_feed(db)
        await _fetches(db, failing, "S", "F", "F", "F")
        reset = await _make_feed(db)  # a success resets the streak: the newest failure counts alone
        await _fetches(db, reset, "F", "F", "S", "F")
        inflight = await _make_feed(db)  # an unfinished fetch neither breaks nor extends a streak
        await _fetches(db, inflight, "F", "F", "Q")
        capped = await _make_feed(db)
        await _fetches(db, capped, *("F" * 25))
        overdue = await _make_feed(db, next_poll_at=NOW - timedelta(hours=3))
        await _fetches(db, overdue, "S")
        late_but_fine = await _make_feed(db, next_poll_at=NOW - timedelta(minutes=5))
        await _fetches(db, late_but_fine, "S")
        awaiting = await _make_feed(db)
        disabled = await _make_feed(db, enabled=False)
        await _fetches(db, disabled, "F")
        retired = await _make_feed(db, retired_at=NOW)
        after = await queries.feeds(db, NOW, HOURS)
        await db.rollback()
    rows = {str(r.id): r for r in after.items}
    state = lambda f: rows[str(f.id)].state  # noqa: E731
    assert (state(ok), state(failing), state(reset), state(inflight)) == ("ok",) + ("failing",) * 3
    assert (state(capped), state(overdue), state(late_but_fine)) == ("failing", "overdue", "ok")
    assert (state(awaiting), state(disabled)) == ("awaiting", "disabled")
    assert str(retired.id) not in rows
    assert [rows[str(f.id)].failure_streak for f in (ok, failing, reset, inflight)] == [0, 3, 1, 2]
    assert rows[str(capped.id)].failure_streak == 20 and rows[str(capped.id)].streak_capped
    assert not rows[str(failing.id)].streak_capped
    assert rows[str(failing.id)].failures_by_category == {"http_transient": 3}
    assert rows[str(overdue.id)].overdue_seconds == 3 * 3600
    assert rows[str(late_but_fine.id)].overdue_seconds == 300
    assert rows[str(awaiting.id)].last_fetch_status is None
    assert rows[str(inflight.id)].last_fetch_status == "failed"
    # Fleet totals: entries - invalid - new = duplicates, with the counts they come from.
    t0, t1 = before.totals, after.totals
    assert t1.fetches - t0.fetches == 40  # finished fetches in the window; the queued one is not
    assert t1.entries - t0.entries == 10 * 5  # five successes: ok, failing, reset, overdue, late
    assert t1.invalid - t0.invalid == 2 * 5
    assert t1.new - t0.new == 3 * 5
    assert t1.duplicates == t1.entries - t1.invalid - t1.new


async def test_fetches_outside_the_window_are_not_totalled() -> None:
    async with session_factory() as db:
        before = (await queries.feeds(db, NOW, HOURS)).totals
        item = await _make_feed(db)
        await _fetches(db, item, "S", start=OUTSIDE)
        after = (await queries.feeds(db, NOW, HOURS)).totals
        await db.rollback()
    assert after.entries == before.entries


async def test_the_feed_list_uses_a_fixed_number_of_statements() -> None:
    async def count(db) -> int:  # type: ignore[no-untyped-def]
        seen: list[str] = []

        def record(conn, cursor, statement, *_):  # type: ignore[no-untyped-def]
            seen.append(statement)

        sa_event.listen(Engine, "before_cursor_execute", record)
        try:
            await queries.feeds(db, NOW, HOURS)
        finally:
            sa_event.remove(Engine, "before_cursor_execute", record)
        return len(seen)

    async with session_factory() as db:
        for _ in range(3):
            await _fetches(db, await _make_feed(db), "S", "F")
        few = await count(db)
        for _ in range(30):
            await _fetches(db, await _make_feed(db), "S", "F", "F")
        many = await count(db)
        await db.rollback()
    assert few == many


# -- failures and storage -----------------------------------------------------------------------


async def test_failures_group_by_category_and_bound_the_recent_list() -> None:
    async with session_factory() as db:
        item = await _make_feed(db)
        for i in range(4):
            db.add(
                FeedFetch(
                    feed_id=item.id, claim_token=uuid.uuid4().hex, status="failed",
                    started_at=INSIDE - timedelta(minutes=i), completed_at=INSIDE,
                    error_category="timeout" if i < 3 else "security", error_message="x" * 900,
                )
            )  # fmt: skip
        db.add(  # outside the window
            FeedFetch(
                feed_id=item.id, claim_token=uuid.uuid4().hex, status="failed",
                started_at=OUTSIDE, completed_at=OUTSIDE, error_category="timeout",
            )
        )  # fmt: skip
        await db.flush()
        page = await queries.failures(db, NOW, "feed", HOURS, 2)
        again = await queries.failures(db, NOW, "feed", HOURS, 2)
        # Other tests' failures can be newer than ours, so look for ours in a longer list.
        wide = await queries.failures(db, NOW, "feed", HOURS, 1000)
        await db.rollback()
    ours = {c.category: c.count for c in page.by_category}
    assert ours["timeout"] >= 3 and ours["security"] >= 1
    assert len(page.recent) == 2 and page.recent == again.recent
    assert page.recent[0].at >= page.recent[1].at
    mine = [f for f in wide.recent if f.ref_id == item.id]
    assert len(mine) == 4
    assert all(len(f.message or "") <= 300 for f in mine)


async def test_monitor_failures_carry_a_category_and_time_but_no_message_or_owner() -> None:
    async with session_factory() as db:
        user = User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(user)
        await db.flush()
        db.add(
            Monitor(
                user_id=user.id, name="hidden-name", kind="search", state_version=1,
                state={"q": "hidden-query"}, error_category="search_unavailable",
                error_message="private detail", last_evaluated_at=INSIDE,
            )
        )  # fmt: skip
        await db.flush()
        page = await queries.failures(db, NOW, "monitor", HOURS, 50)
        await db.rollback()
    text = page.model_dump_json()
    assert page.by_category and page.recent
    for secret in ("hidden-name", "hidden-query", "private detail"):
        assert secret not in text
    assert all(r.message is None and r.ref_id is None for r in page.recent)


async def test_an_nlp_failure_keeps_its_category_after_the_job_recovers() -> None:
    token = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        source = await feed(db)
        article = await _article(db, source, INSIDE)
        state = ArticleNlpState(
            article_id=article.id, processor_name="entities", input_fingerprint=DIGEST,
            processor_version="t", configuration_fingerprint=DIGEST,
        )  # fmt: skip
        db.add(state)
        await db.flush()
        job = NlpJob(
            state_id=state.id, article_id=article.id, processor_name="entities", generation=1,
            input_fingerprint=DIGEST, processor_version="t", configuration_fingerprint=DIGEST,
            status="running", next_attempt_at=INSIDE, claim_token=token,
            claim_expires_at=NOW + timedelta(hours=1),
        )  # fmt: skip
        db.add(job)
    await nlp_execution._record_failure(job.id, token, ConfigurationError("bad model"))
    async with session_factory() as db, db.begin():
        # A later successful attempt clears the job's category, as `_publish` does.
        recovered = await db.get(NlpJob, job.id)
        assert recovered is not None and recovered.error_category == "configuration"
        recovered.error_category, recovered.status = None, "succeeded"
    async with session_factory() as db:
        page = await queries.failures(db, datetime.now(UTC), "nlp", HOURS, 100)
    [mine] = [r for r in page.recent if r.ref_id == article.id]
    assert mine.error_category == "configuration"


async def test_search_failures_include_retrying_deliveries_with_a_cause() -> None:
    async with session_factory() as db:
        source = await feed(db)
        target = SearchIndexTarget(
            index_name=f"idx-{uuid.uuid4().hex}", schema_version=1, role="retained"
        )
        db.add(target)
        await db.flush()
        article = await _article(db, source, INSIDE)
        db.add(
            SearchDelivery(
                article_id=article.id, target_id=target.id, requested_revision=1, status="retrying",
                error_category="elasticsearch_transient", error_message="connection refused",
                updated_at=INSIDE,
            )
        )  # fmt: skip
        await db.flush()
        page = await queries.failures(db, NOW, "search", HOURS, 100)
        await db.rollback()
    assert any(
        r.status == "retrying" and r.error_category == "elasticsearch_transient"
        for r in page.recent
    )


async def test_storage_lists_a_fixed_set_of_tables_with_sizes() -> None:
    async with session_factory() as db:
        result = await queries.storage(db)
    names = [t.name for t in result.tables]
    assert names == list(queries.STORAGE_TABLES)
    assert result.database_bytes > 0
    articles = next(t for t in result.tables if t.name == "articles")
    assert articles.total_bytes > 0 and articles.approximate_rows >= 0
    assert result.retained_html_objects >= 0
    assert result.article_files_measured is False


# -- HTTP ---------------------------------------------------------------------------------------

ROUTES = [
    ("/operations/health", {}),
    ("/operations/pipelines", {}),
    ("/operations/feeds", {}),
    ("/operations/storage", {}),
    ("/operations/failures", {"area": "feed"}),
]


@pytest.mark.parametrize(("path", "params"), ROUTES)
async def test_every_operations_route_needs_a_session(path: str, params: dict[str, Any]) -> None:
    assert await _status(path, signed_in=False, **params) == 401
    assert await _status(path, **params) == 200


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/operations/pipelines", {"hours": 0}),
        ("/operations/pipelines", {"hours": 169}),
        ("/operations/feeds", {"hours": 0}),
        ("/operations/failures", {"area": "nope"}),
        ("/operations/failures", {"area": "feed", "hours": 500}),
        ("/operations/failures", {"area": "feed", "limit": 101}),
        ("/operations/failures", {}),
    ],
)
async def test_bad_windows_and_areas_are_rejected(path: str, params: dict[str, Any]) -> None:
    assert await _status(path, **params) == 422


async def test_health_reports_unreachable_dependencies_as_data_within_the_bound() -> None:
    started = time.monotonic()
    body = await _get("/operations/health")
    assert time.monotonic() - started < 6
    probes_ = {p["name"]: p for p in body["probes"]}
    assert probes_["postgres"]["state"] == "ok" and probes_["postgres"]["latency_ms"] is not None
    # The acceptance script closes the Elasticsearch port; the API still answers, with a state.
    if get_settings().elasticsearch_url.endswith(":1"):
        assert probes_["elasticsearch"]["state"] == "down"
    assert {"redis", "scheduler", "nlp"} <= set(probes_)
    assert all(p["state"] in ("ok", "degraded", "down", "unknown") for p in body["probes"])
    assert "redis://" not in str(body) and "http://" not in str(body)
    assert body["generated_at"]


async def test_the_pipelines_route_has_a_fixed_statement_count() -> None:
    from test_compare_postgres import _statements

    first = await _statements("/operations/pipelines", hours=1)
    assert first == await _statements("/operations/pipelines", hours=168)
    assert first <= 16
