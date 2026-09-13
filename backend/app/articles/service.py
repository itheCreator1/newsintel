import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.feeds.models import ArticleContent, ArticleProcessingAttempt, ArticleProcessingJob

ACTIVE_STATUSES = ("queued", "running", "retrying")
MODE_STRENGTH = {"full_text": 1, "full_text_html": 2}


async def request_processing(
    db: AsyncSession,
    article_id: uuid.UUID,
    requested_mode: str,
    *,
    automatic: bool = False,
) -> tuple[ArticleProcessingJob, bool]:
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
    return job, False


async def claim_due_job(
    db: AsyncSession, job_id: uuid.UUID, lease_seconds: int
) -> tuple[ArticleProcessingJob, str] | None:
    now = datetime.now(UTC)
    job = await db.scalar(
        select(ArticleProcessingJob).where(ArticleProcessingJob.id == job_id).with_for_update()
    )
    if not job or job.status not in ACTIVE_STATUSES or job.next_attempt_at > now:
        return None
    if job.claim_expires_at and job.claim_expires_at > now:
        return None
    token = uuid.uuid4().hex
    job.claim_token = token
    job.claim_expires_at = now + timedelta(seconds=lease_seconds)
    job.status = "running"
    job.started_at = job.started_at or now
    await db.commit()
    return job, token


async def start_attempt(db: AsyncSession, job: ArticleProcessingJob) -> ArticleProcessingAttempt:
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


def due_filter(now: datetime) -> ColumnElement[bool]:
    return (
        ArticleProcessingJob.status.in_(("queued", "retrying", "running"))
        & (ArticleProcessingJob.next_attempt_at <= now)
        & or_(
            ArticleProcessingJob.claim_expires_at.is_(None),
            ArticleProcessingJob.claim_expires_at <= now,
        )
    )
