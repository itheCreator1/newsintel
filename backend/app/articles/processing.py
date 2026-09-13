import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
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
    if isinstance(exc, httpx.HTTPError):
        return "network", True
    return "internal", False


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
            job.next_attempt_at = now + timedelta(seconds=next_retry_delay(stage_attempts))
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
        await start_attempt(db, job)
        stage, url, temporary_key = job.stage, article.original_url, job.temporary_html_key
    try:
        if stage == "fetch":
            response = await fetch_page_http(url, settings)
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    "Transient article response",
                    request=httpx.Request("GET", url),
                    response=httpx.Response(response.status_code),
                )
            if response.status_code >= 400:
                raise ValueError(f"Article returned HTTP {response.status_code}")
            new_key = storage.put(response.content)
            async with session_factory() as db, db.begin():
                job = await db.scalar(
                    select(ArticleProcessingJob)
                    .where(ArticleProcessingJob.id == job_id)
                    .with_for_update()
                )
                if not job or job.claim_token != token:
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
                    attempt.completed_at = datetime.now(UTC)
                job.temporary_html_key = new_key
                job.stage = "extract"
                job.status = "queued"
                job.claim_token = None
                job.claim_expires_at = None
                job.next_attempt_at = datetime.now(UTC)
            return
        html = storage.get(temporary_key or "")
        if html is None:
            async with session_factory() as db, db.begin():
                job = await db.scalar(
                    select(ArticleProcessingJob)
                    .where(ArticleProcessingJob.id == job_id)
                    .with_for_update()
                )
                if job and job.claim_token == token:
                    job.stage = "fetch"
                    job.status = "queued"
                    job.claim_token = None
                    job.claim_expires_at = None
            return
        extractor = TrafilaturaExtractor()
        text = extractor.extract(html)
        digest = hashlib.sha256(text.encode()).hexdigest()
        now = datetime.now(UTC)
        async with session_factory() as db, db.begin():
            job = await db.scalar(
                select(ArticleProcessingJob)
                .where(ArticleProcessingJob.id == job_id)
                .with_for_update()
            )
            if not job or job.claim_token != token:
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
                if content.content_hash != digest:
                    content.previous_content_hash = content.content_hash
                    content.content_hash = digest
                    content.text = text
                    content.change_count += 1
                    content.last_content_change_at = now
                content.extractor_name = extractor.name
                content.extractor_version = extractor.version
                content.extracted_at = now
                if retained_key:
                    content.html_object_key = retained_key
            job.status = "succeeded"
            job.completed_at = now
            job.claim_token = None
            job.claim_expires_at = None
            job.temporary_html_key = None
        if not retained_key and temporary_key:
            storage.delete(temporary_key)
    except Exception as exc:
        await _record_failure(job_id, token, exc)
