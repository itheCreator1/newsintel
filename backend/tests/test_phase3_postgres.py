import os
import uuid

import pytest

from app.articles.routes import retry_job
from app.db.session import session_factory
from app.feeds.models import Article, ArticleProcessingJob

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _article() -> Article:
    unique = uuid.uuid4().hex
    return Article(
        original_url=f"https://news.example/{unique}",
        normalized_url=f"https://news.example/{unique}",
        title="Phase 3 fixture article",
        normalized_title_hash=unique,
    )


async def test_retry_does_not_replace_an_existing_active_jobs_stage_or_object() -> None:
    active_object = uuid.uuid4().hex
    failed_object = uuid.uuid4().hex
    async with session_factory() as db:
        article = _article()
        db.add(article)
        await db.flush()
        active = ArticleProcessingJob(
            article_id=article.id,
            requested_mode="full_text_html",
            stage="fetch",
            status="queued",
            temporary_html_key=active_object,
        )
        failed = ArticleProcessingJob(
            article_id=article.id,
            requested_mode="full_text",
            stage="extract",
            status="failed",
            temporary_html_key=failed_object,
        )
        db.add_all([active, failed])
        await db.commit()
        active_id, failed_id = active.id, failed.id

    async with session_factory() as db:
        response = await retry_job(failed_id, db, None)  # type: ignore[arg-type]

    async with session_factory() as db:
        current_active = await db.get(ArticleProcessingJob, active_id)
        current_failed = await db.get(ArticleProcessingJob, failed_id)
        assert response.reused is True
        assert response.job_id == active_id
        assert current_active is not None
        assert current_active.stage == "fetch"
        assert current_active.temporary_html_key == active_object
        assert current_failed is not None
        assert current_failed.temporary_html_key == failed_object
