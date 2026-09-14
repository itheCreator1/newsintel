import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.articles.storage import LocalObjectStorage
from app.articles.storage_locks import lock_storage_keys
from app.feeds.models import ArticleContent, ArticleProcessingJob

OBJECT_KEY = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class CleanupFailure:
    key: str
    error: str


@dataclass(frozen=True)
class CleanupReport:
    scanned: int = 0
    eligible: int = 0
    deleted: int = 0
    failed: int = 0
    failures: tuple[CleanupFailure, ...] = ()


async def _referenced_keys(db: AsyncSession, keys: list[str]) -> set[str]:
    retained = await db.scalars(
        select(ArticleContent.html_object_key).where(ArticleContent.html_object_key.in_(keys))
    )
    temporary = await db.scalars(
        select(ArticleProcessingJob.temporary_html_key).where(
            ArticleProcessingJob.temporary_html_key.in_(keys)
        )
    )
    return {key for key in [*retained, *temporary] if key is not None}


async def _process_batch(
    db: AsyncSession,
    storage: LocalObjectStorage,
    keys: list[str],
    *,
    apply: bool,
) -> tuple[int, int, list[CleanupFailure]]:
    deleted = 0
    failures: list[CleanupFailure] = []
    async with db.begin():
        await lock_storage_keys(db, keys, shared=False)
        referenced = await _referenced_keys(db, keys)
        eligible = [key for key in keys if key not in referenced]
        if apply:
            for key in eligible:
                try:
                    storage.delete(key)
                    deleted += 1
                except OSError as exc:
                    failures.append(CleanupFailure(key=key, error=str(exc)))
    return len(eligible), deleted, failures


async def cleanup_article_storage(
    db: AsyncSession,
    storage: LocalObjectStorage,
    *,
    minimum_age: timedelta,
    apply: bool = False,
    batch_size: int = 500,
    scan_limit: int = 5_000,
    complete_sweep: bool = False,
) -> CleanupReport:
    """Stream and optionally remove old, unreferenced local objects."""
    cutoff = datetime.now(UTC).timestamp() - minimum_age.total_seconds()
    scanned = eligible = deleted = 0
    failures: list[CleanupFailure] = []
    candidates: list[str] = []
    scanned_in_pass = 0

    async def process_candidates() -> None:
        nonlocal eligible, deleted
        if not candidates:
            return
        batch_eligible, batch_deleted, batch_failures = await _process_batch(
            db, storage, candidates, apply=apply
        )
        eligible += batch_eligible
        deleted += batch_deleted
        failures.extend(batch_failures)
        candidates.clear()

    try:
        with os.scandir(storage.root) as entries:
            for entry in entries:
                scanned += 1
                scanned_in_pass += 1
                if entry.is_symlink():
                    failures.append(CleanupFailure(entry.name, "symbolic link rejected"))
                elif not OBJECT_KEY.fullmatch(entry.name):
                    failures.append(CleanupFailure(entry.name, "malformed object key"))
                else:
                    try:
                        if entry.is_file(follow_symlinks=False) and entry.stat(
                            follow_symlinks=False
                        ).st_mtime <= cutoff:
                            candidates.append(entry.name)
                    except OSError as exc:
                        failures.append(CleanupFailure(entry.name, str(exc)))

                if len(candidates) >= batch_size:
                    await process_candidates()
                if scanned_in_pass >= scan_limit:
                    await process_candidates()
                    if not complete_sweep:
                        break
                    scanned_in_pass = 0
        await process_candidates()
    except FileNotFoundError:
        pass
    return CleanupReport(
        scanned=scanned,
        eligible=eligible,
        deleted=deleted,
        failed=len(failures),
        failures=tuple(failures),
    )
