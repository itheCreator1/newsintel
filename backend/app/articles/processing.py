import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import select

from app.articles.extraction import EmptyExtraction, TrafilaturaExtractor
from app.articles.service import start_attempt
from app.articles.storage import LocalObjectStorage, ObjectStorageError
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.http import FeedTooLarge, fetch_page_http
from app.feeds.models import (
    Article,
    ArticleContent,
    ArticleProcessingAttempt,
    ArticleProcessingJob,
)
from app.feeds.network import UnsafeFeedUrl
from app.feeds.scheduling import next_retry_delay

log = structlog.get_logger()


class ArticleHttpStatus(ValueError):
    def __init__(self, status_code: int, retry_after: float | None = None) -> None:
        super().__init__(f"Article returned HTTP {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


def _failure(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, UnsafeFeedUrl):
        return "security", False
    if isinstance(exc, FeedTooLarge):
        return "response_too_large", False
    if isinstance(exc, EmptyExtraction):
        return "empty_extraction", False
    if isinstance(exc, ObjectStorageError):
        return "storage", True
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", True
    if isinstance(exc, ArticleHttpStatus):
        if exc.status_code == 429 or exc.status_code >= 500:
            return "http_transient", True
        return "http_permanent", False
    if isinstance(exc, httpx.HTTPError):
        return "network", True
    return "internal", False


def delete_after_commit(
    storage: LocalObjectStorage, key: str | None, *, article_id: str
) -> bool:
    if not key:
        return True
    try:
        storage.delete(key)
    except ObjectStorageError:
        log.exception(
            "article_object_cleanup_failed",
            article_id=article_id,
            object_key=key,
            stage="cleanup",
        )
        return False
    return True


async def _record_failure(job_id: uuid.UUID, token: str, exc: Exception) -> None:
    category, transient = _failure(exc)
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        job = await db.scalar(
            select(ArticleProcessingJob).where(ArticleProcessingJob.id == job_id).with_for_update()
        )
        if (
            not job
            or job.claim_token != token
            or not job.claim_expires_at
            or job.claim_expires_at <= now
        ):
            return
        attempt = await db.scalar(
            select(ArticleProcessingAttempt)
            .where(ArticleProcessingAttempt.job_id == job.id)
            .order_by(ArticleProcessingAttempt.started_at.desc())
        )
        if attempt:
            attempt.status = "failed"
            attempt.error_category = category
            attempt.error_message = str(exc)[:1000]
            attempt.completed_at = now
        stage_attempts = attempt.attempt_number if attempt else 3
        job.error_category = category
        job.error_message = str(exc)[:1000]
        job.claim_token = None
        job.claim_expires_at = None
        if transient and stage_attempts < 3:
            job.status = "retrying"
            retry_after = exc.retry_after if isinstance(exc, ArticleHttpStatus) else None
            job.next_attempt_at = now + timedelta(
                seconds=next_retry_delay(stage_attempts, retry_after=retry_after)
            )
        else:
            job.status = "failed"
            job.completed_at = now


async def process_claim(job_id: uuid.UUID, token: str) -> None:
    settings = get_settings()
    storage = LocalObjectStorage(settings.article_storage_path)
    async with session_factory() as db, db.begin():
        job = await db.scalar(
            select(ArticleProcessingJob).where(ArticleProcessingJob.id == job_id).with_for_update()
        )
        now = datetime.now(UTC)
        if (
            not job
            or job.claim_token != token
            or not job.claim_expires_at
            or job.claim_expires_at <= now
        ):
            return
        article = await db.get(Article, job.article_id)
        if not article:
            return
        attempt = await start_attempt(db, job)
        if attempt is None:
            return
        stage, url, temporary_key = job.stage, article.original_url, job.temporary_html_key
    try:
        if stage == "fetch":
            response = await fetch_page_http(url, settings)
            if response.status_code >= 400:
                retry_after_value = response.headers.get("retry-after")
                try:
                    retry_after = float(retry_after_value) if retry_after_value else None
                except ValueError:
                    retry_after = None
                raise ArticleHttpStatus(response.status_code, retry_after)
            new_key = storage.put(response.content)
            async with session_factory() as db, db.begin():
                job = await db.scalar(
                    select(ArticleProcessingJob)
                    .where(ArticleProcessingJob.id == job_id)
                    .with_for_update()
                )
                now = datetime.now(UTC)
                if (
                    not job
                    or job.claim_token != token
                    or not job.claim_expires_at
                    or job.claim_expires_at <= now
                ):
                    storage.delete(new_key)
                    return
                attempt = await db.scalar(
                    select(ArticleProcessingAttempt)
                    .where(ArticleProcessingAttempt.job_id == job.id)
                    .order_by(ArticleProcessingAttempt.started_at.desc())
                )
                if attempt:
                    attempt.status = "succeeded"
                    attempt.http_status = response.status_code
                    attempt.completed_at = now
                job.temporary_html_key = new_key
                job.stage = "extract"
                job.status = "queued"
                job.claim_token = None
                job.claim_expires_at = None
                job.next_attempt_at = now
            return
        html = storage.get(temporary_key or "")
        if html is None:
            async with session_factory() as db, db.begin():
                job = await db.scalar(
                    select(ArticleProcessingJob)
                    .where(ArticleProcessingJob.id == job_id)
                    .with_for_update()
                )
                if (
                    job
                    and job.claim_token == token
                    and job.claim_expires_at
                    and job.claim_expires_at > datetime.now(UTC)
                ):
                    attempt = await db.scalar(
                        select(ArticleProcessingAttempt)
                        .where(
                            ArticleProcessingAttempt.job_id == job.id,
                            ArticleProcessingAttempt.status == "running",
                        )
                        .order_by(ArticleProcessingAttempt.started_at.desc())
                    )
                    if attempt:
                        attempt.status = "failed"
                        attempt.error_category = "temporary_html_missing"
                        attempt.error_message = "Temporary HTML object is missing"
                        attempt.completed_at = datetime.now(UTC)
                    job.stage = "fetch"
                    job.status = "queued"
                    job.temporary_html_key = None
                    job.claim_token = None
                    job.claim_expires_at = None
            return
        extractor = TrafilaturaExtractor()
        text = extractor.extract(html)
        digest = hashlib.sha256(text.encode()).hexdigest()
        now = datetime.now(UTC)
        old_html_key: str | None = None
        async with session_factory() as db, db.begin():
            job = await db.scalar(
                select(ArticleProcessingJob)
                .where(ArticleProcessingJob.id == job_id)
                .with_for_update()
            )
            if (
                not job
                or job.claim_token != token
                or not job.claim_expires_at
                or job.claim_expires_at <= now
            ):
                return
            attempt = await db.scalar(
                select(ArticleProcessingAttempt)
                .where(ArticleProcessingAttempt.job_id == job.id)
                .order_by(ArticleProcessingAttempt.started_at.desc())
            )
            if attempt:
                attempt.status = "succeeded"
                attempt.completed_at = now
            content = await db.get(ArticleContent, job.article_id, with_for_update=True)
            retained_key = temporary_key if job.requested_mode == "full_text_html" else None
            if content is None:
                content = ArticleContent(
                    article_id=job.article_id,
                    text=text,
                    content_hash=digest,
                    previous_content_hash=None,
                    change_count=0,
                    extractor_name=extractor.name,
                    extractor_version=extractor.version,
                    extracted_at=now,
                    last_content_change_at=now,
                    html_object_key=retained_key,
                )
                db.add(content)
            else:
                old_html_key = content.html_object_key
                if content.content_hash != digest:
                    content.previous_content_hash = content.content_hash
                    content.content_hash = digest
                    content.text = text
                    content.change_count += 1
                    content.last_content_change_at = now
                content.extractor_name = extractor.name
                content.extractor_version = extractor.version
                content.extracted_at = now
                content.html_object_key = retained_key
            job.status = "succeeded"
            job.completed_at = now
            job.claim_token = None
            job.claim_expires_at = None
            job.temporary_html_key = None
        if not retained_key:
            delete_after_commit(storage, temporary_key, article_id=str(job.article_id))
        if content is not None and old_html_key and old_html_key != retained_key:
            delete_after_commit(storage, old_html_key, article_id=str(job.article_id))
    except Exception as exc:
        await _record_failure(job_id, token, exc)
