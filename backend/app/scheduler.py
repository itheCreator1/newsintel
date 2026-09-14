import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select

from app.articles.service import claim_due_job, due_filter
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import ArticleProcessingJob, Feed
from app.feeds.service import claim_feed
from app.jobs.articles import process_article
from app.jobs.ingestion import ingest_feed
from app.jobs.search import index_article
from app.search.models import SearchDelivery
from app.search.service import claim_delivery, delivery_due

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


async def run_scheduler(interval_seconds: float = 10) -> None:
    while True:
        try:
            await schedule_due_feeds()
            await schedule_due_articles()
            await schedule_due_search()
        except Exception:
            log.exception("scheduler_cycle_failed")
        await asyncio.sleep(interval_seconds)


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
