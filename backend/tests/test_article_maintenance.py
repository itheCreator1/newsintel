import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.articles.maintenance import CleanupReport, cleanup_article_storage
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


async def test_cleanup_counts_and_reports_malformed_entries_and_symlinks(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    orphan = storage.put(b"orphan")
    _age(tmp_path / orphan)
    (tmp_path / "malformed").write_bytes(b"bad")
    (tmp_path / "link").symlink_to(tmp_path / orphan)

    async with session_factory() as db:
        report = await cleanup_article_storage(
            db,
            storage,
            minimum_age=timedelta(hours=24),
            apply=True,
            scan_limit=2,
        )

    assert report.scanned == 2
    assert report.failed >= 1
    assert report.failures
    assert (tmp_path / "link").is_symlink()


async def test_complete_sweep_continues_through_successive_bounded_batches(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(tmp_path)
    keys = [storage.put(str(index).encode()) for index in range(5)]
    for key in keys:
        _age(tmp_path / key)

    async with session_factory() as db:
        report = await cleanup_article_storage(
            db,
            storage,
            minimum_age=timedelta(hours=24),
            apply=True,
            batch_size=1,
            scan_limit=2,
            complete_sweep=True,
        )

    assert (report.scanned, report.eligible, report.deleted, report.failed) == (5, 5, 5, 0)
    assert all(storage.get(key) is None for key in keys)


async def test_cleanup_failure_identifies_the_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalObjectStorage(tmp_path)
    orphan = storage.put(b"orphan")
    _age(tmp_path / orphan)

    def fail_delete(key: str) -> None:
        raise OSError(f"cannot delete {key}")

    monkeypatch.setattr(storage, "delete", fail_delete)
    async with session_factory() as db:
        report = await cleanup_article_storage(
            db, storage, minimum_age=timedelta(hours=24), apply=True
        )

    assert report.failed == 1
    assert report.failures[0].key == orphan
    assert "cannot delete" in report.failures[0].error


async def test_cleanup_lock_preserves_object_published_concurrently(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    key = storage.put(b"publication")
    _age(tmp_path / key)
    article_id = uuid.uuid4()
    publication_locked = asyncio.Event()
    release_publication = asyncio.Event()

    async def publish_reference() -> None:
        from app.articles.storage_locks import lock_storage_keys

        async with session_factory() as db, db.begin():
            await lock_storage_keys(db, [key], shared=True)
            db.add(
                Article(
                    id=article_id,
                    original_url=f"https://example.com/{article_id}",
                    normalized_url=f"https://example.com/{article_id}",
                    title="Concurrent publication",
                    normalized_title_hash=uuid.uuid4().hex,
                )
            )
            db.add(
                ArticleContent(
                    article_id=article_id,
                    text="published",
                    content_hash="b" * 64,
                    change_count=0,
                    extractor_name="test",
                    extractor_version="1",
                    extracted_at=datetime.now(UTC),
                    last_content_change_at=datetime.now(UTC),
                    html_object_key=key,
                )
            )
            await db.flush()
            publication_locked.set()
            await release_publication.wait()

    async def clean() -> CleanupReport:
        async with session_factory() as db:
            return await cleanup_article_storage(
                db, storage, minimum_age=timedelta(hours=24), apply=True
            )

    publishing = asyncio.create_task(publish_reference())
    await publication_locked.wait()
    cleanup = asyncio.create_task(clean())
    await asyncio.sleep(0.1)
    assert not cleanup.done()
    release_publication.set()
    await publishing
    report = await cleanup
    async with session_factory() as db:
        referenced = await db.scalar(
            select(ArticleContent.html_object_key).where(ArticleContent.article_id == article_id)
        )

    assert report.deleted == 0
    assert referenced == key
    assert storage.get(key) == b"publication"
