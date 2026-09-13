from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.articles.storage import LocalObjectStorage, ObjectStorageError
from app.feeds.models import ArticleContent, ArticleProcessingJob


@dataclass(frozen=True)
class CleanupReport:
    scanned: int = 0
    eligible: int = 0
    deleted: int = 0
    failed: int = 0


async def _is_referenced(db: AsyncSession, key: str) -> bool:
    retained = await db.scalar(
        select(ArticleContent.article_id).where(ArticleContent.html_object_key == key).limit(1)
    )
    if retained is not None:
        return True
    job = await db.scalar(
        select(ArticleProcessingJob.id)
        .where(ArticleProcessingJob.temporary_html_key == key)
        .limit(1)
    )
    return job is not None


async def cleanup_article_storage(
    db: AsyncSession,
    storage: LocalObjectStorage,
    *,
    minimum_age: timedelta,
    apply: bool = False,
    batch_size: int = 500,
    scan_limit: int = 5_000,
) -> CleanupReport:
    """Report or remove a bounded batch of old, unreferenced local objects."""
    cutoff = datetime.now(UTC).timestamp() - minimum_age.total_seconds()
    scanned = eligible = deleted = failed = 0
    try:
        entries = storage.root.iterdir()
        for entry in entries:
            if scanned >= scan_limit or eligible >= batch_size:
                break
            if not entry.is_file() or len(entry.name) != 32 or not entry.name.isalnum():
                continue
            scanned += 1
            if entry.stat().st_mtime > cutoff or await _is_referenced(db, entry.name):
                continue
            eligible += 1
            if not apply:
                continue
            # Object keys are freshly generated. The age window exceeds an active
            # processing lease, and this final check protects references committed
            # while the inventory was being scanned.
            if await _is_referenced(db, entry.name):
                eligible -= 1
                continue
            try:
                storage.delete(entry.name)
                deleted += 1
            except ObjectStorageError:
                failed += 1
    except FileNotFoundError:
        pass
    return CleanupReport(scanned=scanned, eligible=eligible, deleted=deleted, failed=failed)
