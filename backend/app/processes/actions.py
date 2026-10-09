"""What the Processes page can do: retry what failed, run a scheduled process now, stop a run.

Nothing here commits, except fetching a feed (the worker must see the claim first); the routes
commit. Each action goes through the service the process already has, so a retry here is the same
as a retry from the process's own page.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import and_, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.articles.service import retry_processing
from app.clustering.models import ArticleClusterState, ClusterJob
from app.clustering.service import request_clustering
from app.core.config import Settings
from app.feeds.models import ArticleProcessingJob, Feed, FeedFetch
from app.feeds.service import QueueUnavailable, fetch_now, send_fetch
from app.monitors.models import Monitor
from app.nlp.models import ArticleNlpState, NlpJob, NlpReprocessingRun
from app.nlp.service import request_article_nlp
from app.processes.schemas import ProcessKey
from app.processes.states import RETRYABLE, RUNNABLE, STOPPABLE
from app.search.models import SearchDelivery
from app.wikidata import runs as wikidata_runs
from app.wikidata.models import WikidataRun
from app.wikidata.status import disabled_reason

log = structlog.get_logger()
# How many failed items one "Retry all failed" takes; the answer says how many remain.
RETRY_BATCH = 200


def _send_events(sweep: bool) -> None:
    from app.jobs.events import associate_events

    associate_events.send(sweep)


def _send_retention() -> None:
    from app.jobs.maintenance import prune_history

    prune_history.send()


@dataclass(frozen=True)
class Senders:
    """The queue each action sends to; tests record the messages instead."""

    feed: Callable[[str, str], None] = field(default=send_fetch)
    events: Callable[[bool], None] = field(default=_send_events)
    retention: Callable[[], None] = field(default=_send_retention)


def default_senders() -> Senders:
    return Senders()


class Refused(Exception):
    """The action does not apply: the routes answer with this status and detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RunNow:
    status: Literal["sent", "queued"]
    run_id: uuid.UUID | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _supported(key: ProcessKey, keys: tuple[ProcessKey, ...], what: str) -> None:
    if key not in keys:
        raise Refused(422, f"This process cannot {what}")


# -- retrying -----------------------------------------------------------------------------------


async def _retry_article(db: AsyncSession, job_id: uuid.UUID) -> bool:
    job = await db.get(ArticleProcessingJob, job_id)
    if job is None or job.status != "failed":
        return False
    await retry_processing(db, job_id)
    return True


async def _retry_nlp(db: AsyncSession, job_id: uuid.UUID) -> bool:
    job = await db.get(NlpJob, job_id)
    if job is None or job.status != "failed":
        return False
    await request_article_nlp(db, job.article_id, processor_names=(job.processor_name,), force=True)
    return True


async def _retry_cluster(db: AsyncSession, job_id: uuid.UUID) -> bool:
    job = await db.get(ClusterJob, job_id)
    if job is None or job.status != "failed":
        return False
    await request_clustering(db, job.article_id)
    return True


async def _retry_deliveries(db: AsyncSession, ids: list[uuid.UUID]) -> int:
    """Queue failed deliveries again, due now, as the search page's retry does."""
    if not ids:
        return 0
    result = await db.execute(
        update(SearchDelivery)
        .where(SearchDelivery.id.in_(ids), SearchDelivery.status == "failed")
        .values(status="queued", next_attempt_at=_now(), error_category=None, error_message=None)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _retry_monitors(db: AsyncSession, ids: list[uuid.UUID]) -> int:
    """Make monitors in error due now; their next evaluation records how it went."""
    if not ids:
        return 0
    result = await db.execute(
        update(Monitor)
        .where(Monitor.id.in_(ids), Monitor.enabled.is_(True), Monitor.error_category.is_not(None))
        .values(next_evaluation_at=_now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _fetch(db: AsyncSession, feed_id: uuid.UUID, senders: Senders) -> bool:
    try:
        return await fetch_now(db, feed_id, senders.feed) is not None
    except QueueUnavailable:
        log.warning("feed_retry_queue_failed", feed_id=str(feed_id))
        raise


async def retry_item(
    db: AsyncSession, key: ProcessKey, item_id: uuid.UUID, senders: Senders
) -> bool:
    """Try one failed row of the activity list again; False when the row did not fail.

    Raises QueueUnavailable when a feed's fetch could not be queued.
    """
    _supported(key, RETRYABLE, "be retried")
    if key == "articles":
        return await _retry_article(db, item_id)
    if key == "nlp":
        return await _retry_nlp(db, item_id)
    if key == "clustering":
        return await _retry_cluster(db, item_id)
    if key == "search":
        return await _retry_deliveries(db, [item_id]) > 0
    if key == "monitors":
        return await _retry_monitors(db, [item_id]) > 0
    fetch = await db.get(FeedFetch, item_id)
    if fetch is None or fetch.status != "failed":
        return False
    return await _fetch(db, fetch.feed_id, senders)


def _failed_articles() -> Any:
    """Each article's newest job, when it failed: an older failure a later job replaced (or one
    already being retried) is not retried again."""
    newer = aliased(ArticleProcessingJob)
    job = ArticleProcessingJob
    return select(job.id).where(
        job.status == "failed",
        ~exists().where(
            newer.article_id == job.article_id,
            (newer.created_at > job.created_at)
            | and_(newer.created_at == job.created_at, newer.id > job.id),
        ),
    )


def _failed_nlp() -> Any:
    """Failed jobs of the generation still requested; a newer generation replaced the others."""
    return (
        select(NlpJob.id)
        .join(ArticleNlpState, ArticleNlpState.id == NlpJob.state_id)
        .where(NlpJob.status == "failed", NlpJob.generation == ArticleNlpState.requested_generation)
    )


def _failed_clusters() -> Any:
    return (
        select(ClusterJob.id)
        .join(ArticleClusterState, ArticleClusterState.id == ClusterJob.state_id)
        .where(
            ClusterJob.status == "failed",
            ClusterJob.generation == ArticleClusterState.requested_generation,
        )
    )


def _failing_feeds() -> Any:
    """Live feeds whose newest finished fetch failed."""
    newest = (
        select(FeedFetch.status)
        .where(FeedFetch.feed_id == Feed.id, FeedFetch.status.in_(("success", "failed")))
        .order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    return select(Feed.id).where(
        Feed.enabled.is_(True), Feed.retired_at.is_(None), newest == "failed"
    )


_CANDIDATES: dict[str, Callable[[], Any]] = {
    "articles": _failed_articles,
    "nlp": _failed_nlp,
    "clustering": _failed_clusters,
    "search": lambda: select(SearchDelivery.id).where(SearchDelivery.status == "failed"),
    "monitors": lambda: select(Monitor.id).where(
        Monitor.enabled.is_(True), Monitor.error_category.is_not(None)
    ),
    "feeds": _failing_feeds,
}


async def retry_failed(
    db: AsyncSession, key: ProcessKey, senders: Senders
) -> tuple[int, int]:
    """Try up to RETRY_BATCH failed items of a process again: (retried, still failed)."""
    _supported(key, RETRYABLE, "be retried")
    candidates = _CANDIDATES[key]().subquery()
    total = int(await db.scalar(select(func.count()).select_from(candidates)) or 0)
    ids = list(
        await db.scalars(select(candidates.c.id).order_by(candidates.c.id).limit(RETRY_BATCH))
    )
    retried = 0
    if key == "search":
        retried = await _retry_deliveries(db, ids)
    elif key == "monitors":
        retried = await _retry_monitors(db, ids)
    else:
        one: dict[str, Callable[[uuid.UUID], Awaitable[bool]]] = {
            "articles": lambda item: _retry_article(db, item),
            "nlp": lambda item: _retry_nlp(db, item),
            "clustering": lambda item: _retry_cluster(db, item),
        }
        for item in ids:
            if key == "feeds":
                try:
                    done = await _fetch(db, item, senders)
                except QueueUnavailable:
                    # The queue is down: the rest would fail the same way.
                    break
            else:
                done = await one[key](item)
            retried += int(done)
    return retried, max(total - retried, 0)


# -- run now ------------------------------------------------------------------------------------

WIKIDATA_KINDS: dict[str, str] = {
    "wikidata_refresh": "refresh", "wikidata_candidates": "candidates",
}  # fmt: skip


async def _queue_wikidata(db: AsyncSession, kind: str) -> WikidataRun:
    """The sweep of this kind: the unfinished one, else a new one for the scheduler to hand out."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": wikidata_runs.CLAIM_LOCK})
    unfinished = await db.scalar(
        select(WikidataRun)
        .where(
            WikidataRun.kind == kind,
            WikidataRun.entity_id.is_(None),
            WikidataRun.status.in_(wikidata_runs.UNFINISHED),
        )
        .order_by(WikidataRun.created_at, WikidataRun.id)
        .limit(1)
    )
    if unfinished is not None:
        return unfinished
    run = WikidataRun(kind=kind)
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def run_now(
    db: AsyncSession, key: ProcessKey, settings: Settings, senders: Senders
) -> RunNow:
    """Run a scheduled process now, without waiting for its next turn."""
    _supported(key, RUNNABLE, "be run by hand")
    if key in WIKIDATA_KINDS:
        reason = disabled_reason(settings)
        if reason is not None:
            raise Refused(409, f"Wikidata is switched off: {reason}")
        run = await _queue_wikidata(db, WIKIDATA_KINDS[key])
        return RunNow(status="queued", run_id=run.id)
    try:
        if key == "events":
            # A full sweep: a run by hand looks at every story, not only the changed ones.
            senders.events(True)
        else:
            senders.retention()
    except Exception as exc:
        log.exception("process_queue_failed", process=key)
        raise Refused(503, "The job queue is unavailable") from exc
    return RunNow(status="sent")


# -- stop ---------------------------------------------------------------------------------------


async def stop(db: AsyncSession, key: ProcessKey, run_id: uuid.UUID) -> bool:
    """End a run part way; False when it is not running. What it did so far stays done."""
    _supported(key, STOPPABLE, "be stopped")
    if key == "reprocessing":
        run = await db.scalar(
            select(NlpReprocessingRun)
            .where(NlpReprocessingRun.id == run_id, NlpReprocessingRun.status == "scanning")
            .with_for_update()
        )
        if run is None:
            return False
        run.status = "stopped"
        run.completed_at = _now()
        return True
    wikidata = await db.scalar(
        select(WikidataRun)
        .where(
            WikidataRun.id == run_id,
            WikidataRun.kind == WIKIDATA_KINDS[key],
            WikidataRun.status.in_(wikidata_runs.UNFINISHED),
        )
        .with_for_update()
    )
    if wikidata is None:
        return False
    # The worker holding it sees the status at its next write and stops there.
    wikidata.status = "stopped"
    wikidata.claim_token = None
    wikidata.claim_expires_at = None
    wikidata.finished_at = _now()
    return True
