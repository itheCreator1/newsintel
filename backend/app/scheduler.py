import asyncio
import time
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy import or_, select

from app.articles.service import claim_due_job, due_filter
from app.clustering.models import ClusterJob
from app.clustering.service import claim_job as claim_cluster_job
from app.clustering.service import job_due as cluster_job_due
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import ArticleProcessingJob, Feed
from app.feeds.service import claim_feed
from app.jobs.articles import process_article
from app.jobs.clustering import process_clustering
from app.jobs.events import associate_events
from app.jobs.ingestion import ingest_feed
from app.jobs.monitors import evaluate_monitor
from app.jobs.nlp import process_nlp
from app.jobs.search import index_article
from app.monitors.evaluation import claim_monitor, monitor_due
from app.monitors.models import Monitor
from app.nlp.models import NlpJob
from app.nlp.service import claim_job as claim_nlp_job
from app.nlp.service import job_due as nlp_job_due
from app.operations import heartbeat, retention
from app.search.models import SearchDelivery, SourceSearchRefresh
from app.search.service import (
    claim_delivery,
    delivery_due,
    process_source_refresh,
)

log = structlog.get_logger()


async def schedule_due_feeds(batch_size: int = 50) -> int:
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(Feed.id)
                    .where(
                        Feed.enabled.is_(True),
                        Feed.retired_at.is_(None),
                        Feed.next_poll_at <= datetime.now(UTC),
                        or_(
                            Feed.claim_expires_at.is_(None),
                            Feed.claim_expires_at <= datetime.now(UTC),
                        ),
                    )
                    .order_by(Feed.next_poll_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    queued = 0
    for feed_id in ids:
        async with session_factory() as db:
            claimed = await claim_feed(db, feed_id)
            if not claimed:
                continue
            fetch, reused = claimed
            if reused:
                continue
            try:
                ingest_feed.send(str(feed_id), fetch.claim_token)
                queued += 1
            except Exception as exc:
                log.error(
                    "feed_queue_failed",
                    feed_id=str(feed_id),
                    fetch_id=str(fetch.id),
                    error_category="queue",
                )
                feed = await db.get(Feed, feed_id, with_for_update=True)
                if feed and feed.claim_token == fetch.claim_token:
                    feed.claim_token = None
                    feed.claim_expires_at = None
                    fetch.status = "failed"
                    fetch.error_category = "queue"
                    fetch.error_message = str(exc)[:1000]
                    fetch.completed_at = datetime.now(UTC)
                    await db.commit()
    return queued


async def schedule_due_articles(batch_size: int = 50) -> int:
    now = datetime.now(UTC)
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(ArticleProcessingJob.id)
                    .where(due_filter(now))
                    .order_by(ArticleProcessingJob.next_attempt_at, ArticleProcessingJob.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    queued = 0
    for job_id in ids:
        async with session_factory() as db:
            claimed = await claim_due_job(db, job_id, get_settings().article_lease_seconds)
            if not claimed:
                continue
            _, token = claimed
        try:
            process_article.send(str(job_id), token)
            queued += 1
        except Exception:
            # The PostgreSQL lease expires, making this work visible again.
            log.exception("article_queue_failed", job_id=str(job_id))
    return queued


async def schedule_due_search(batch_size: int = 100) -> int:
    now = datetime.now(UTC)
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(SearchDelivery.id)
                    .where(delivery_due(now))
                    .order_by(SearchDelivery.next_attempt_at, SearchDelivery.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    queued = 0
    for delivery_id in ids:
        async with session_factory() as db:
            claimed = await claim_delivery(db, delivery_id, get_settings().search_lease_seconds)
        if claimed is None:
            continue
        _, token = claimed
        try:
            index_article.send(str(delivery_id), token)
            queued += 1
        except Exception:
            log.exception("search_queue_failed", delivery_id=str(delivery_id))
    return queued


async def schedule_due_nlp(batch_size: int = 50) -> int:
    now = datetime.now(UTC)
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(NlpJob.id)
                    .where(nlp_job_due(now))
                    .order_by(NlpJob.next_attempt_at, NlpJob.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    queued = 0
    for job_id in ids:
        async with session_factory() as db:
            claimed = await claim_nlp_job(db, job_id, get_settings().nlp_lease_seconds)
        if claimed is None:
            continue
        _, token = claimed
        try:
            process_nlp.send(str(job_id), token)
            queued += 1
        except Exception:
            log.exception("nlp_queue_failed", job_id=str(job_id))
    return queued


async def schedule_due_clustering(batch_size: int = 50) -> int:
    now = datetime.now(UTC)
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(ClusterJob.id)
                    .where(cluster_job_due(now))
                    .order_by(ClusterJob.next_attempt_at, ClusterJob.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    queued = 0
    for job_id in ids:
        async with session_factory() as db:
            claimed = await claim_cluster_job(db, job_id, get_settings().clustering_lease_seconds)
        if claimed is None:
            continue
        _, token = claimed
        try:
            process_clustering.send(str(job_id), token)
            queued += 1
        except Exception:
            log.exception("clustering_queue_failed", job_id=str(job_id))
    return queued


async def schedule_due_monitors(batch_size: int = 50) -> int:
    now = datetime.now(UTC)
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(Monitor.id)
                    .where(monitor_due(now))
                    .order_by(Monitor.next_evaluation_at, Monitor.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    queued = 0
    for monitor_id in ids:
        async with session_factory() as db:
            token = await claim_monitor(db, monitor_id, get_settings().monitor_lease_seconds)
        if token is None:
            continue
        try:
            evaluate_monitor.send(str(monitor_id), token)
            queued += 1
        except Exception:
            log.exception("monitor_queue_failed", monitor_id=str(monitor_id))
    return queued


EVENT_INTERVAL_SECONDS = 30
EVENT_SWEEP_SECONDS = 300
_event_runs = {"run": float("-inf"), "sweep": float("-inf")}


async def schedule_due_events() -> bool:
    """Queue one association run per interval; changed clusters are found in PostgreSQL, so a
    lost or failed run costs only latency. The throttle is per scheduler process.
    """
    now = time.monotonic()
    if now - _event_runs["run"] < EVENT_INTERVAL_SECONDS:
        return False
    sweep = now - _event_runs["sweep"] >= EVENT_SWEEP_SECONDS
    try:
        associate_events.send(sweep)
    except Exception:
        log.exception("event_queue_failed")
        return False
    _event_runs["run"] = now
    if sweep:
        _event_runs["sweep"] = now
    return True


RETENTION_INTERVAL_SECONDS = 3600
_retention = {"run": float("-inf")}


async def schedule_retention() -> None:
    """Prune succeeded job history hourly (per scheduler process), on the first cycle too."""
    now = time.monotonic()
    if now - _retention["run"] < RETENTION_INTERVAL_SECONDS:
        return
    _retention["run"] = now
    async with session_factory() as db, db.begin():
        deleted = await retention.prune_history(db, datetime.now(UTC))
    if any(deleted.values()):
        log.info("history_pruned", **deleted)


async def schedule_source_refreshes(batch_size: int = 10) -> int:
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(SourceSearchRefresh.id)
                    .where(SourceSearchRefresh.status.in_(("queued", "running", "retrying")))
                    .order_by(SourceSearchRefresh.next_attempt_at, SourceSearchRefresh.id)
                    .limit(batch_size)
                )
            ).all()
        )
    for refresh_id in ids:
        await process_source_refresh(refresh_id)
    return len(ids)


async def run_scheduler(interval_seconds: float = 10) -> None:
    redis = Redis.from_url(get_settings().redis_url)
    while True:
        try:
            await schedule_due_feeds()
            await schedule_due_articles()
            await schedule_due_nlp()
            await schedule_due_clustering()
            await schedule_due_search()
            await schedule_due_monitors()
            await schedule_due_events()
            await schedule_source_refreshes()
            await schedule_retention()
        except Exception:
            log.exception("scheduler_cycle_failed")
        try:  # a failing cycle still means the process is alive; a Redis blip only logs
            await heartbeat.beat(redis, "scheduler")
        except Exception:
            log.exception("scheduler_heartbeat_failed")
        await asyncio.sleep(interval_seconds)


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
