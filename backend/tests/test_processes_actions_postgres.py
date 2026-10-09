"""What the Processes page can do: retry what failed, run a scheduled process now, stop a run."""

import os
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from event_fixtures import DIGEST, feed
from sqlalchemy import func, select
from test_compare_postgres import _article

from app.auth.models import Session, User
from app.auth.routes import current_session
from app.clustering.models import ArticleClusterState, ClusterJob
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import ArticleProcessingJob, Feed, FeedFetch
from app.main import create_app
from app.monitors.models import Monitor
from app.nlp import reprocessing
from app.nlp.models import ArticleNlpState, NlpJob, NlpReprocessingRun
from app.processes import actions, activity, summary
from app.search.models import SearchDelivery, SearchIndexTarget
from app.wikidata import runs as wikidata_runs
from app.wikidata.models import WikidataRun

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
NOW = datetime.now(UTC)
INSIDE = NOW - timedelta(hours=2)
LATER = NOW + timedelta(hours=1)
SETTINGS = get_settings().model_copy(update={"wikidata_contact": "owner@example.org"})
OFF = get_settings().model_copy(update={"wikidata_enabled": False})
CSRF = {"X-CSRF-Token": "csrf-token"}


class Sent:
    """Stands in for a Dramatiq actor's `send`, recording each message."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> None:
        self.calls.append(args)


def _senders() -> actions.Senders:
    return actions.Senders(feed=Sent(), events=Sent(), retention=Sent())


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    login = Session(
        id=uuid.uuid4(), user_id=uuid.uuid4(), csrf_token="csrf-token",
        token_hash=uuid.uuid4().hex, expires_at=datetime.now(UTC) + timedelta(hours=1),
    )  # fmt: skip
    app.dependency_overrides[current_session] = lambda: login
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver/api/v1"
    ) as client:
        yield client


async def _nlp_job(db, article, status: str, generation: int = 1) -> NlpJob:  # type: ignore[no-untyped-def]
    state = await db.scalar(
        select(ArticleNlpState).where(
            ArticleNlpState.article_id == article.id, ArticleNlpState.processor_name == "keywords"
        )
    )
    if state is None:
        state = ArticleNlpState(
            article_id=article.id, processor_name="keywords", input_fingerprint=DIGEST,
            processor_version="t", configuration_fingerprint=DIGEST,
        )  # fmt: skip
        db.add(state)
        await db.flush()
    state.requested_generation = generation
    job = NlpJob(
        state_id=state.id, article_id=article.id, processor_name="keywords",
        generation=generation, input_fingerprint=DIGEST, processor_version="t",
        configuration_fingerprint=DIGEST, status=status,
    )  # fmt: skip
    db.add(job)
    await db.flush()
    return job


async def _cluster_job(db, article, status: str, generation: int = 1) -> ClusterJob:  # type: ignore[no-untyped-def]
    state = await db.scalar(
        select(ArticleClusterState).where(ArticleClusterState.article_id == article.id)
    )
    if state is None:
        state = ArticleClusterState(article_id=article.id, algorithm_version="rule-1")
        db.add(state)
        await db.flush()
    state.requested_generation = generation
    job = ClusterJob(
        state_id=state.id, article_id=article.id, generation=generation,
        algorithm_version="rule-1", status=status,
    )  # fmt: skip
    db.add(job)
    await db.flush()
    return job


async def _monitor(db, **kw: Any) -> Monitor:  # type: ignore[no-untyped-def]
    user = User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(user)
    await db.flush()
    row = Monitor(user_id=user.id, name=uuid.uuid4().hex, kind="search", state_version=1,
                  state={"q": "x"}, **kw)  # fmt: skip
    db.add(row)
    await db.flush()
    return row


async def _failing_feed() -> uuid.UUID:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        db.add(FeedFetch(feed_id=source.id, claim_token=uuid.uuid4().hex, status="failed",
                         started_at=INSIDE, completed_at=INSIDE))  # fmt: skip
        return source.id


async def _retire(feed_id: uuid.UUID) -> None:
    """Fetching a feed commits (the claim must be visible to the worker), so these tests
    retire what they made: no other test's scheduler step then finds it due."""
    async with session_factory() as db, db.begin():
        source = await db.get(Feed, feed_id)
        assert source is not None
        source.retired_at, source.claim_token, source.claim_expires_at = NOW, None, None


# -- retrying one row ---------------------------------------------------------------------------


async def test_retrying_a_failed_article_job_queues_a_new_job_for_the_article() -> None:
    async with session_factory() as db:
        art = await _article(db, await feed(db), INSIDE)
        job = ArticleProcessingJob(article_id=art.id, requested_mode="rss", status="failed",
                                   completed_at=INSIDE)  # fmt: skip
        db.add(job)
        await db.flush()
        assert await actions.retry_item(db, "articles", job.id, _senders())
        queued = await db.scalar(
            select(func.count()).where(
                ArticleProcessingJob.article_id == art.id, ArticleProcessingJob.status == "queued"
            )
        )
        await db.rollback()
    assert queued == 1


async def test_retrying_a_failed_nlp_or_cluster_job_requests_a_new_generation() -> None:
    async with session_factory() as db:
        art = await _article(db, await feed(db), INSIDE)
        nlp = await _nlp_job(db, art, "failed")
        cluster = await _cluster_job(db, art, "failed")
        assert await actions.retry_item(db, "nlp", nlp.id, _senders())
        assert await actions.retry_item(db, "clustering", cluster.id, _senders())
        nlp_new = await db.scalar(
            select(NlpJob.generation).where(NlpJob.article_id == art.id, NlpJob.status == "queued")
        )
        cluster_new = await db.scalar(
            select(ClusterJob.generation).where(
                ClusterJob.article_id == art.id, ClusterJob.status == "queued"
            )
        )
        await db.rollback()
    assert (nlp_new, cluster_new) == (2, 2)


async def test_retrying_a_failed_delivery_or_monitor_makes_it_due_now() -> None:
    async with session_factory() as db:
        art = await _article(db, await feed(db), INSIDE)
        target = SearchIndexTarget(index_name=f"idx-{uuid.uuid4().hex}", schema_version=3,
                                   role="current")  # fmt: skip
        db.add(target)
        await db.flush()
        delivery = SearchDelivery(article_id=art.id, target_id=target.id, requested_revision=1,
                                  status="failed", error_category="mapping",
                                  next_attempt_at=LATER)  # fmt: skip
        monitor = await _monitor(db, error_category="search", next_evaluation_at=LATER)
        db.add(delivery)
        await db.flush()
        assert await actions.retry_item(db, "search", delivery.id, _senders())
        assert await actions.retry_item(db, "monitors", monitor.id, _senders())
        await db.flush()
        await db.refresh(delivery)
        await db.refresh(monitor)
        await db.rollback()
    assert (delivery.status, delivery.error_category) == ("queued", None)
    assert delivery.next_attempt_at <= datetime.now(UTC)
    assert monitor.next_evaluation_at <= datetime.now(UTC)


async def test_retrying_a_failed_feed_fetch_fetches_the_feed_now() -> None:
    feed_id = await _failing_feed()
    async with session_factory() as db:
        fetch_id = await db.scalar(select(FeedFetch.id).where(FeedFetch.feed_id == feed_id))
        sent = _senders()
        assert await actions.retry_item(db, "feeds", fetch_id, sent)
        queued = await db.scalar(
            select(FeedFetch).where(FeedFetch.feed_id == feed_id, FeedFetch.status == "queued")
        )
    await _retire(feed_id)
    assert queued is not None
    assert sent.feed.calls == [(str(feed_id), queued.claim_token)]  # type: ignore[attr-defined]


@pytest.mark.parametrize("key", ["articles", "nlp", "clustering", "search", "monitors", "feeds"])
async def test_a_row_that_did_not_fail_is_not_retried(key: str) -> None:
    async with session_factory() as db:
        assert not await actions.retry_item(db, key, uuid.uuid4(), _senders())
        await db.rollback()


# -- retrying everything that failed ------------------------------------------------------------


async def test_retry_failed_skips_failures_a_newer_job_already_replaced() -> None:
    async with session_factory() as db:
        source = await feed(db)
        again, fixed = await _article(db, source, INSIDE), await _article(db, source, INSIDE)
        db.add_all(
            [
                ArticleProcessingJob(article_id=again.id, requested_mode="rss", status="failed",
                                     created_at=INSIDE, completed_at=INSIDE),
                ArticleProcessingJob(article_id=fixed.id, requested_mode="rss", status="failed",
                                     created_at=INSIDE, completed_at=INSIDE),
                ArticleProcessingJob(article_id=fixed.id, requested_mode="rss",
                                     status="succeeded", created_at=NOW, completed_at=NOW),
            ]
        )  # fmt: skip
        old_nlp = await _nlp_job(db, fixed, "failed", generation=1)
        await _nlp_job(db, fixed, "succeeded", generation=2)
        new_nlp = await _nlp_job(db, again, "failed", generation=1)
        await _cluster_job(db, fixed, "failed", generation=1)
        await _cluster_job(db, fixed, "succeeded", generation=2)
        await _cluster_job(db, again, "failed", generation=1)
        await db.flush()
        results = {key: await actions.retry_failed(db, key, _senders())
                   for key in ("articles", "nlp", "clustering")}  # fmt: skip

        async def queued(model: Any, article: Any) -> int:
            return int(await db.scalar(
                select(func.count()).where(model.article_id == article.id, model.status == "queued")
            ) or 0)  # fmt: skip

        counts = {
            name: (await queued(model, again), await queued(model, fixed))
            for name, model in (("articles", ArticleProcessingJob), ("nlp", NlpJob),
                                ("clustering", ClusterJob))
        }  # fmt: skip
        await db.rollback()
    assert counts == {"articles": (1, 0), "nlp": (1, 0), "clustering": (1, 0)}
    assert all(retried >= 1 for retried, _ in results.values())
    assert old_nlp.id != new_nlp.id


async def test_retry_failed_works_in_bounded_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "RETRY_BATCH", 1)
    async with session_factory() as db:
        source = await feed(db)
        for _ in range(2):
            art = await _article(db, source, INSIDE)
            db.add(ArticleProcessingJob(article_id=art.id, requested_mode="rss", status="failed",
                                        completed_at=INSIDE))  # fmt: skip
        await db.flush()
        retried, remaining = await actions.retry_failed(db, "articles", _senders())
        await db.rollback()
    assert retried == 1 and remaining >= 1


async def test_retry_failed_fetches_every_failing_feed_now() -> None:
    feed_id = await _failing_feed()
    sent = _senders()
    async with session_factory() as db:
        retried, _ = await actions.retry_failed(db, "feeds", sent)
    for call in sent.feed.calls:  # type: ignore[attr-defined]
        await _retire(uuid.UUID(call[0]))
    assert retried >= 1
    assert str(feed_id) in {call[0] for call in sent.feed.calls}  # type: ignore[attr-defined]


async def test_retry_failed_makes_broken_monitors_due_and_failed_deliveries_queued() -> None:
    async with session_factory() as db:
        monitor = await _monitor(db, error_category="search", next_evaluation_at=LATER)
        art = await _article(db, await feed(db), INSIDE)
        target = SearchIndexTarget(index_name=f"idx-{uuid.uuid4().hex}", schema_version=3,
                                   role="current")  # fmt: skip
        db.add(target)
        await db.flush()
        delivery = SearchDelivery(article_id=art.id, target_id=target.id, requested_revision=1,
                                  status="failed", next_attempt_at=LATER)  # fmt: skip
        db.add(delivery)
        await db.flush()
        await actions.retry_failed(db, "monitors", _senders())
        await actions.retry_failed(db, "search", _senders())
        await db.flush()
        await db.refresh(monitor)
        await db.refresh(delivery)
        await db.rollback()
    assert monitor.next_evaluation_at <= datetime.now(UTC)
    assert delivery.status == "queued"


# -- run now ------------------------------------------------------------------------------------


async def test_running_event_linking_or_the_cleanup_now_sends_their_jobs() -> None:
    sent = _senders()
    async with session_factory() as db:
        assert (await actions.run_now(db, "events", SETTINGS, sent)).status == "sent"
        assert (await actions.run_now(db, "retention", SETTINGS, sent)).status == "sent"
        await db.rollback()
    assert sent.events.calls == [(True,)]  # type: ignore[attr-defined]
    assert sent.retention.calls == [()]  # type: ignore[attr-defined]


@pytest.mark.parametrize(("key", "kind"), [("wikidata_refresh", "refresh"),
                                           ("wikidata_candidates", "candidates")])  # fmt: skip
async def test_running_wikidata_now_queues_one_run_and_reuses_an_unfinished_one(
    key: str, kind: str
) -> None:
    async with session_factory() as db:
        await db.execute(WikidataRun.__table__.delete())
        first = await actions.run_now(db, key, SETTINGS, _senders())
        second = await actions.run_now(db, key, SETTINGS, _senders())
        rows = list(await db.scalars(select(WikidataRun).where(WikidataRun.kind == kind)))
        await db.rollback()
    assert (first.status, second.status) == ("queued", "queued")
    assert first.run_id == second.run_id == rows[0].id and len(rows) == 1
    assert rows[0].entity_id is None and rows[0].status == "queued"


async def test_wikidata_cannot_run_while_it_is_switched_off() -> None:
    async with session_factory() as db:
        with pytest.raises(actions.Refused) as refused:
            await actions.run_now(db, "wikidata_refresh", OFF, _senders())
        await db.rollback()
    assert refused.value.status_code == 409 and "switched off" in refused.value.detail


# -- stop ---------------------------------------------------------------------------------------


async def test_a_stopped_reprocessing_run_queues_no_more_articles() -> None:
    async with session_factory() as db, db.begin():
        run = NlpReprocessingRun(status="scanning", processor_names=["keywords"],
                                 selection={"all": True})  # fmt: skip
        db.add(run)
    async with session_factory() as db, db.begin():
        assert await actions.stop(db, "reprocessing", run.id)
    assert await reprocessing.scan_reprocessing(run.id) == 0
    async with session_factory() as db:
        stopped = await db.get(NlpReprocessingRun, run.id)
    assert stopped is not None and stopped.status == "stopped"
    assert stopped.completed_at is not None and stopped.scanned_count == 0


async def test_a_stopped_wikidata_run_ends_and_its_sweep_waits_for_tomorrow() -> None:
    async with session_factory() as db, db.begin():
        await db.execute(WikidataRun.__table__.delete())
        run = WikidataRun(kind="candidates", status="running", claim_token=uuid.uuid4(),
                          claim_expires_at=LATER, started_at=NOW)  # fmt: skip
        db.add(run)
    try:
        async with session_factory() as db, db.begin():
            assert await actions.stop(db, "wikidata_candidates", run.id)
        async with session_factory() as db, db.begin():
            stopped = await db.get(WikidataRun, run.id)
            assert stopped is not None
            assert (stopped.status, stopped.claim_token) == ("stopped", None)
            assert stopped.finished_at is not None
            # The scheduler does not queue the day's sweep again straight away.
            assert await wikidata_runs.ensure_sweep(db, SETTINGS) is None
    finally:
        async with session_factory() as db, db.begin():
            await db.execute(WikidataRun.__table__.delete().where(WikidataRun.id == run.id))


@pytest.mark.parametrize("key", ["reprocessing", "wikidata_refresh", "wikidata_candidates"])
async def test_a_run_that_is_not_active_cannot_be_stopped(key: str) -> None:
    async with session_factory() as db:
        assert not await actions.stop(db, key, uuid.uuid4())
        await db.rollback()


# -- what the page offers -----------------------------------------------------------------------


async def test_cards_and_rows_say_which_actions_they_offer() -> None:
    async with session_factory() as db:
        await db.execute(NlpReprocessingRun.__table__.delete())
        await db.execute(WikidataRun.__table__.delete())
        source = await feed(db)
        art = await _article(db, source, INSIDE)
        art.title = f"Offer {uuid.uuid4().hex}"
        job = ArticleProcessingJob(article_id=art.id, requested_mode="rss", status="failed",
                                   created_at=LATER, completed_at=LATER)  # fmt: skip
        run = NlpReprocessingRun(status="scanning", processor_names=["keywords"],
                                 selection={"all": True}, created_at=LATER)  # fmt: skip
        db.add_all([job, run])
        await db.flush()
        cards = {c.key: c for c in (await summary.summary(db, NOW, 24, SETTINGS)).processes}
        off = {c.key: c for c in (await summary.summary(db, NOW, 24, OFF)).processes}
        page = await activity.activity(db, NOW, 24, process=None, status="all", q=None,
                                       after=None, limit=100, user_id=None)  # fmt: skip
        await db.rollback()
    assert "retry_failed" in cards["articles"].actions
    assert cards["reprocessing"].actions == ["stop"]
    assert cards["events"].actions == ["run_now"] and cards["retention"].actions == ["run_now"]
    assert cards["wikidata_refresh"].actions == ["run_now"]
    assert off["wikidata_refresh"].actions == []
    assert "stop" not in cards["authority"].actions and "stop" not in cards["rebuild"].actions
    rows = {item.id: item for item in page.items}
    assert (rows[job.id].can_retry, rows[job.id].can_stop) == (True, False)
    assert (rows[run.id].can_retry, rows[run.id].can_stop) == (False, True)


# -- the routes ---------------------------------------------------------------------------------


async def test_the_action_routes_need_the_csrf_token_and_reject_other_processes() -> None:
    async with _client() as client:
        assert (await client.post("/processes/events/run")).status_code == 403
        assert (await client.post("/processes/rebuild/run", headers=CSRF)).status_code == 422
        assert (await client.post("/processes/events/retry-failed", headers=CSRF)).status_code == 422
        missing = f"/processes/reprocessing/runs/{uuid.uuid4()}/stop"
        assert (await client.post(missing, headers=CSRF)).status_code == 409
        row = f"/processes/activity/articles/{uuid.uuid4()}/retry"
        assert (await client.post(row, headers=CSRF)).status_code == 404


async def test_retry_failed_answers_how_many_it_retried() -> None:
    async with session_factory() as db, db.begin():
        monitor = await _monitor(db, error_category="search", next_evaluation_at=LATER)
    async with _client() as client:
        response = await client.post("/processes/monitors/retry-failed", headers=CSRF)
    assert response.status_code == 202
    assert response.json()["retried"] >= 1 and "remaining" in response.json()
    async with session_factory() as db:
        due = await db.scalar(select(Monitor.next_evaluation_at).where(Monitor.id == monitor.id))
    assert due is not None and due <= datetime.now(UTC)


async def test_feeds_that_are_retired_are_not_fetched() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        db.add(FeedFetch(feed_id=source.id, claim_token=uuid.uuid4().hex, status="failed",
                         started_at=INSIDE, completed_at=INSIDE))  # fmt: skip
        source.retired_at = NOW
    sent = _senders()
    async with session_factory() as db:
        await actions.retry_failed(db, "feeds", sent)
    for call in sent.feed.calls:  # type: ignore[attr-defined]
        await _retire(uuid.UUID(call[0]))
    assert str(source.id) not in {c[0] for c in sent.feed.calls}  # type: ignore[attr-defined]
