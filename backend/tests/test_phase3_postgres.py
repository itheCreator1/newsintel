import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.articles.processing import ArticleHttpStatus, _record_failure, process_claim
from app.articles.routes import retry_job
from app.articles.service import claim_due_job, request_processing, start_attempt
from app.core.config import Settings
from app.db.session import session_factory
from app.feeds.models import (
    Article,
    ArticleContent,
    ArticleProcessingAttempt,
    ArticleProcessingJob,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
FIXTURE_PORT = os.getenv("NEWSINTEL_TEST_FIXTURE_PORT", "18080")


async def _article(url: str) -> uuid.UUID:
    article_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Article(
                id=article_id,
                original_url=url,
                normalized_url=f"https://archive.example/{article_id}",
                title="Processing lifecycle",
                normalized_title_hash=uuid.uuid4().hex,
            )
        )
        await db.commit()
    return article_id


async def _run_stage(job_id: uuid.UUID) -> None:
    async with session_factory() as db:
        claimed = await claim_due_job(db, job_id, 300)
    assert claimed is not None
    await process_claim(job_id, claimed[1])


async def test_successful_extraction_retains_html_and_tracks_content_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        article_storage_path=str(tmp_path),
        feed_test_allowed_hosts=["localhost"],
        article_host_min_interval_seconds=0,
    )
    monkeypatch.setattr("app.articles.processing.get_settings", lambda: settings)
    article_id = await _article(f"http://localhost:{FIXTURE_PORT}/article.html")
    async with session_factory() as db:
        job, _ = await request_processing(db, article_id, "full_text_html")
        await db.commit()
        job_id = job.id
    await _run_stage(job_id)
    await _run_stage(job_id)

    async with session_factory() as db:
        content = await db.get(ArticleContent, article_id)
        assert content is not None
        first_hash = content.content_hash
        assert "first readable fixture" in content.text
        assert content.html_object_key is not None
        assert (tmp_path / content.html_object_key).exists()
        unchanged, _ = await request_processing(db, article_id, "full_text_html")
        await db.commit()
        unchanged_id = unchanged.id
    await _run_stage(unchanged_id)
    await _run_stage(unchanged_id)
    async with session_factory() as db:
        content = await db.get(ArticleContent, article_id)
        assert content is not None
        assert (content.content_hash, content.previous_content_hash, content.change_count) == (
            first_hash,
            None,
            0,
        )
        article = await db.get(Article, article_id)
        assert article is not None
        article.original_url = f"http://localhost:{FIXTURE_PORT}/article-changed.html"
        changed, _ = await request_processing(db, article_id, "full_text_html")
        await db.commit()
        changed_id = changed.id
    await _run_stage(changed_id)
    await _run_stage(changed_id)
    async with session_factory() as db:
        content = await db.get(ArticleContent, article_id)
        assert content is not None
        assert content.content_hash != first_hash
        assert content.previous_content_hash == first_hash
        assert content.change_count == 1


async def test_expired_claim_is_recovered_with_a_new_token() -> None:
    article_id = await _article("https://example.com/lease")
    async with session_factory() as db:
        job, _ = await request_processing(db, article_id, "full_text")
        await db.commit()
        first = await claim_due_job(db, job.id, 300)
        assert first is not None
        claimed = await db.get(ArticleProcessingJob, job.id)
        assert claimed is not None
        claimed.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        recovered = await claim_due_job(db, job.id, 300)
        assert recovered is not None and recovered[1] != first[1]


async def test_concurrent_processing_requests_share_one_active_job() -> None:
    article_id = await _article("https://example.com/concurrent")

    async def request() -> tuple[uuid.UUID, bool]:
        async with session_factory() as db:
            job, reused = await request_processing(db, article_id, "full_text")
            await db.commit()
            return job.id, reused

    results = await asyncio.gather(request(), request())
    assert results[0][0] == results[1][0]
    assert sorted(reused for _, reused in results) == [False, True]


async def test_transient_failures_exhaust_three_attempts() -> None:
    article_id = await _article("https://example.com/retries")
    async with session_factory() as db:
        job, _ = await request_processing(db, article_id, "full_text")
        await db.commit()
        job_id = job.id
    for attempt_number in range(1, 4):
        async with session_factory() as db:
            job = await db.get(ArticleProcessingJob, job_id)
            assert job is not None
            job.next_attempt_at = datetime.now(UTC)
            await db.commit()
            claimed = await claim_due_job(db, job_id, 300)
            assert claimed is not None
            await start_attempt(db, claimed[0])
            await db.commit()
        await _record_failure(job_id, claimed[1], ArticleHttpStatus(503))
        async with session_factory() as db:
            job = await db.get(ArticleProcessingJob, job_id)
            assert job is not None
            assert job.status == ("failed" if attempt_number == 3 else "retrying")
    async with session_factory() as db:
        attempts = await db.get(ArticleProcessingJob, job_id)
        assert attempts is not None and attempts.error_category == "http_transient"
        recovered = await retry_job(job_id, db, None)  # type: ignore[arg-type]
        assert recovered.job_id != job_id and recovered.status == "queued"


async def test_retry_does_not_overwrite_an_existing_active_job() -> None:
    article_id = uuid.uuid4()
    failed_id = uuid.uuid4()
    active_id = uuid.uuid4()
    active_key = uuid.uuid4().hex
    failed_key = uuid.uuid4().hex
    async with session_factory() as db:
        db.add(
            Article(
                id=article_id,
                original_url=f"https://example.com/{article_id}",
                normalized_url=f"https://example.com/{article_id}",
                title="Retry isolation",
                normalized_title_hash=uuid.uuid4().hex,
            )
        )
        db.add_all(
            [
                ArticleProcessingJob(
                    id=failed_id,
                    article_id=article_id,
                    requested_mode="full_text_html",
                    stage="extract",
                    status="failed",
                    temporary_html_key=failed_key,
                ),
                ArticleProcessingJob(
                    id=active_id,
                    article_id=article_id,
                    requested_mode="full_text",
                    stage="fetch",
                    status="queued",
                    temporary_html_key=active_key,
                ),
            ]
        )
        await db.commit()

        response = await retry_job(failed_id, db, None)  # type: ignore[arg-type]

        active = await db.get(ArticleProcessingJob, active_id)
        failed = await db.get(ArticleProcessingJob, failed_id)
        assert response.job_id == active_id and response.reused is True
        assert active is not None
        assert (active.stage, active.temporary_html_key) == ("fetch", active_key)
        assert failed is not None and failed.temporary_html_key == failed_key


async def test_duplicate_claim_delivery_starts_only_one_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        article_storage_path=str(tmp_path),
        feed_test_allowed_hosts=["localhost"],
        article_host_min_interval_seconds=0,
    )
    monkeypatch.setattr("app.articles.processing.get_settings", lambda: settings)
    article_id = await _article(f"http://localhost:{FIXTURE_PORT}/article.html")
    async with session_factory() as db:
        job, _ = await request_processing(db, article_id, "full_text")
        await db.commit()
        claimed = await claim_due_job(db, job.id, 300)
        assert claimed is not None

    await asyncio.gather(
        process_claim(job.id, claimed[1]),
        process_claim(job.id, claimed[1]),
    )

    async with session_factory() as db:
        attempts = list(
            await db.scalars(
                select(ArticleProcessingAttempt).where(
                    ArticleProcessingAttempt.job_id == job.id,
                    ArticleProcessingAttempt.stage == "fetch",
                )
            )
        )
        assert len(attempts) == 1


async def test_reclaim_finishes_abandoned_attempt_and_rejects_stale_completion() -> None:
    article_id = await _article("https://example.com/stale-completion")
    async with session_factory() as db:
        job, _ = await request_processing(db, article_id, "full_text")
        await db.commit()
        first = await claim_due_job(db, job.id, 300)
        assert first is not None
        await start_attempt(db, first[0])
        first[0].claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        recovered = await claim_due_job(db, job.id, 300)
        assert recovered is not None

    await _record_failure(job.id, first[1], ArticleHttpStatus(503))

    async with session_factory() as db:
        attempts = list(
            await db.scalars(
                select(ArticleProcessingAttempt)
                .where(ArticleProcessingAttempt.job_id == job.id)
                .order_by(ArticleProcessingAttempt.started_at)
            )
        )
        current = await db.get(ArticleProcessingJob, job.id)
        assert len(attempts) == 1
        assert (attempts[0].status, attempts[0].error_category) == (
            "failed",
            "lease_expired",
        )
        assert attempts[0].completed_at is not None
        assert current is not None
        assert (current.status, current.claim_token) == ("running", recovered[1])


async def test_missing_temporary_html_requeues_fetch_and_finishes_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article_id = await _article("https://example.com/missing-temporary-html")
    async with session_factory() as db:
        job = ArticleProcessingJob(
            article_id=article_id,
            requested_mode="full_text_html",
            stage="extract",
            status="queued",
            temporary_html_key=uuid.uuid4().hex,
        )
        db.add(job)
        await db.commit()
        claimed = await claim_due_job(db, job.id, 300)
        assert claimed is not None

    monkeypatch.setattr(
        "app.articles.processing.get_settings",
        lambda: Settings(article_storage_path=str(tmp_path)),
    )
    await process_claim(job.id, claimed[1])

    async with session_factory() as db:
        current = await db.get(ArticleProcessingJob, job.id)
        attempt = await db.scalar(
            select(ArticleProcessingAttempt).where(ArticleProcessingAttempt.job_id == job.id)
        )
        assert current is not None
        assert (current.stage, current.status, current.temporary_html_key) == (
            "fetch",
            "queued",
            None,
        )
        assert attempt is not None
        assert (attempt.status, attempt.error_category) == ("failed", "temporary_html_missing")
        assert attempt.completed_at is not None


async def test_simultaneous_retries_transfer_temporary_html_once() -> None:
    article_id = await _article("https://example.com/simultaneous-retry")
    temporary_key = uuid.uuid4().hex
    async with session_factory() as db:
        failed = ArticleProcessingJob(
            article_id=article_id,
            requested_mode="full_text_html",
            stage="extract",
            status="failed",
            temporary_html_key=temporary_key,
        )
        db.add(failed)
        await db.commit()
        failed_id = failed.id

    async def retry() -> uuid.UUID:
        async with session_factory() as db:
            response = await retry_job(failed_id, db, None)  # type: ignore[arg-type]
            return response.job_id

    retried_ids = await asyncio.gather(retry(), retry())
    assert retried_ids[0] == retried_ids[1]
    async with session_factory() as db:
        old = await db.get(ArticleProcessingJob, failed_id)
        new = await db.get(ArticleProcessingJob, retried_ids[0])
        assert old is not None and old.temporary_html_key is None
        assert new is not None
        assert (new.stage, new.temporary_html_key) == ("extract", temporary_key)
