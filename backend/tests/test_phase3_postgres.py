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
