import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.articles.maintenance import cleanup_article_storage
from app.articles.storage import LocalObjectStorage
from app.db.session import session_factory
from app.feeds.models import Article, ArticleContent, ArticleProcessingJob

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _age(path: Path, hours: int = 48) -> None:
    timestamp = (datetime.now(UTC) - timedelta(hours=hours)).timestamp()
    os.utime(path, (timestamp, timestamp))


async def test_cleanup_preserves_references_and_recent_objects_and_defaults_to_dry_run(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(tmp_path)
    retained, failed_job, orphan, recent = [
        storage.put(value) for value in (b"a", b"b", b"c", b"d")
    ]
    for key in (retained, failed_job, orphan):
        _age(tmp_path / key)
    article_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Article(
                id=article_id,
                original_url=f"https://example.com/{article_id}",
                normalized_url=f"https://example.com/{article_id}",
                title="Cleanup references",
                normalized_title_hash=uuid.uuid4().hex,
            )
        )
        db.add(
            ArticleContent(
                article_id=article_id,
                text="retained",
                content_hash="a" * 64,
                change_count=0,
                extractor_name="test",
                extractor_version="1",
                extracted_at=datetime.now(UTC),
                last_content_change_at=datetime.now(UTC),
                html_object_key=retained,
            )
        )
        db.add(
            ArticleProcessingJob(
                article_id=article_id,
                requested_mode="full_text_html",
                stage="extract",
                status="failed",
                temporary_html_key=failed_job,
            )
        )
        await db.commit()

        report = await cleanup_article_storage(db, storage, minimum_age=timedelta(hours=24))
        assert (report.scanned, report.eligible, report.deleted, report.failed) == (4, 1, 0, 0)
        assert storage.get(orphan) == b"c"

        report = await cleanup_article_storage(
            db, storage, minimum_age=timedelta(hours=24), apply=True
        )
        assert (report.eligible, report.deleted, report.failed) == (1, 1, 0)
        assert storage.get(orphan) is None
        assert storage.get(retained) == b"a"
        assert storage.get(failed_job) == b"b"
        assert storage.get(recent) == b"d"
