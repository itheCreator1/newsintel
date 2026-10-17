import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.articles.models import (
    ACTIVE_STATUSES,
    Article,
    ArticleContent,
    ArticleProcessingAttempt,
    ArticleProcessingJob,
)
from app.articles.storage_locks import lock_storage_keys
from app.jobs.leasing import claim, claimable, due
from app.search.service import request_indexing

MODE_STRENGTH = {"full_text": 1, "full_text_html": 2}


async def request_processing(
    db: AsyncSession,
    article_id: uuid.UUID,
    requested_mode: str,
    *,
    automatic: bool = False,
) -> tuple[ArticleProcessingJob, bool]:
    # Serialize creation even when no active job row exists yet.
    await db.scalar(select(Article.id).where(Article.id == article_id).with_for_update())
    active = await db.scalar(
        select(ArticleProcessingJob)
        .where(
            ArticleProcessingJob.article_id == article_id,
            ArticleProcessingJob.status.in_(ACTIVE_STATUSES),
        )
        .with_for_update()
    )
    if active:
        if MODE_STRENGTH[requested_mode] > MODE_STRENGTH[active.requested_mode]:
            active.requested_mode = requested_mode
        return active, True
    if automatic:
        content = await db.get(ArticleContent, article_id)
        previous = await db.scalar(
            select(ArticleProcessingJob.id).where(ArticleProcessingJob.article_id == article_id)
        )
        if content or previous:
            raise ValueError("automatic processing is not needed")
    job = ArticleProcessingJob(article_id=article_id, requested_mode=requested_mode)
    db.add(job)
    await db.flush()
    await request_indexing(db, article_id)
    return job, False


async def claim_due_job(
    db: AsyncSession, job_id: uuid.UUID, lease_seconds: int
) -> tuple[ArticleProcessingJob, str] | None:
    now = datetime.now(UTC)
    job = await db.scalar(
        select(ArticleProcessingJob).where(ArticleProcessingJob.id == job_id).with_for_update()
    )
    if not job or not claimable(job, now):
        return None
    if job.claim_token and job.claim_expires_at and job.claim_expires_at <= now:
        abandoned = await db.scalars(
            select(ArticleProcessingAttempt).where(
                ArticleProcessingAttempt.job_id == job.id,
                ArticleProcessingAttempt.status == "running",
            )
        )
        for attempt in abandoned:
            attempt.status = "failed"
            attempt.error_category = "lease_expired"
            attempt.error_message = "Processing lease expired before the attempt completed"
            attempt.completed_at = now
    # Attempts are counted per stage, in ArticleProcessingAttempt.
    token = claim(job, now, lease_seconds, count_attempt=False)
    job.started_at = job.started_at or now
    await request_indexing(db, job.article_id)
    await db.commit()
    return job, token


async def start_attempt(
    db: AsyncSession, job: ArticleProcessingJob
) -> ArticleProcessingAttempt | None:
    running = await db.scalar(
        select(ArticleProcessingAttempt.id).where(
            ArticleProcessingAttempt.job_id == job.id,
            ArticleProcessingAttempt.stage == job.stage,
            ArticleProcessingAttempt.status == "running",
        )
    )
    if running is not None:
        return None
    count = await db.scalar(
        select(func.count())
        .select_from(ArticleProcessingAttempt)
        .where(
            ArticleProcessingAttempt.job_id == job.id,
            ArticleProcessingAttempt.stage == job.stage,
        )
    )
    attempt = ArticleProcessingAttempt(
        job_id=job.id, stage=job.stage, attempt_number=int(count or 0) + 1
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def retry_processing(
    db: AsyncSession, failed_job_id: uuid.UUID
) -> tuple[ArticleProcessingJob, bool]:
    snapshot = await db.get(ArticleProcessingJob, failed_job_id)
    if snapshot is None:
        raise LookupError("processing job not found")
    if snapshot.status in ACTIVE_STATUSES:
        return snapshot, True

    await lock_storage_keys(
        db,
        [snapshot.temporary_html_key] if snapshot.temporary_html_key else [],
        shared=True,
    )

    job, reused = await request_processing(db, snapshot.article_id, snapshot.requested_mode)
    if reused:
        return job, True

    failed = await db.scalar(
        select(ArticleProcessingJob)
        .where(ArticleProcessingJob.id == failed_job_id)
        .with_for_update()
    )
    if failed is None:
        raise LookupError("processing job not found")
    temporary_key = failed.temporary_html_key
    failed.temporary_html_key = None
    await db.flush()
    if temporary_key:
        job.stage = failed.stage
        job.temporary_html_key = temporary_key
    return job, False


def due_filter(now: datetime) -> ColumnElement[bool]:
    return due(ArticleProcessingJob, now)
