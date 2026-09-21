from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete

from app.db.session import session_factory
from app.events.engine import (
    EVENT_BATCH,
    BatchResult,
    EventAssociator,
    RuleEventAssociator,
    reconcile_events,
    summary,
)
from app.events.models import EventAssociationRun

log = structlog.get_logger()

# Batches per run; the scheduler starts another run soon, so a backlog drains without one long job.
MAX_BATCHES = 20
# Run rows older than this are deleted whenever a new one is written.
RUN_RETENTION = timedelta(days=30)
_ASSOCIATOR: EventAssociator = RuleEventAssociator()


def associator() -> EventAssociator:
    return _ASSOCIATOR


async def run_association(sweep: bool = False) -> BatchResult:
    """Decide changed clusters in bounded transactions; optionally refresh recent events after."""
    total = BatchResult()
    for _ in range(MAX_BATCHES):
        async with session_factory() as db, db.begin():
            batch = await associator().run_batch(db, EVENT_BATCH)
        total.skipped = batch.skipped
        total.last_error = batch.last_error or total.last_error
        for field in ("evaluated", "created", "deleted", "failed"):
            setattr(total, field, getattr(total, field) + getattr(batch, field))
        if batch.skipped or batch.evaluated + batch.failed < EVENT_BATCH:
            break
    if sweep and not total.skipped:
        async with session_factory() as db, db.begin():
            swept = await reconcile_events(db, associator().version)
        total.deleted += swept.deleted
    return total


async def record_run(
    started_at: datetime, sweep: bool, result: BatchResult, error: str | None
) -> None:
    """Keep a row for a run that did work or failed, so failures outlive the logs."""
    failed = error is not None or result.failed > 0
    async with session_factory() as db, db.begin():
        db.add(
            EventAssociationRun(
                started_at=started_at, completed_at=datetime.now(UTC), sweep=sweep,
                evaluated=result.evaluated, created=result.created, deleted=result.deleted,
                failed=result.failed,
                error_category="event_association" if failed else None,
                error_message=error or result.last_error,
            )
        )  # fmt: skip
        await db.execute(
            delete(EventAssociationRun).where(
                EventAssociationRun.started_at < datetime.now(UTC) - RUN_RETENTION
            )
        )


async def process_events(sweep: bool = False) -> None:
    started_at = datetime.now(UTC)
    try:
        result = await run_association(sweep)
    except Exception as exc:
        log.exception("event_association_run_failed", error_category="event_association")
        await _record(started_at, sweep, BatchResult(), f"{type(exc).__name__}: {exc}"[:1000])
        return
    if result.skipped or result.evaluated or result.failed or result.deleted:
        log.info("event_association", skipped=result.skipped, sweep=sweep, **summary(result))
    if result.evaluated or result.failed or result.deleted:
        await _record(started_at, sweep, result, None)


async def _record(
    started_at: datetime, sweep: bool, result: BatchResult, error: str | None
) -> None:
    try:
        await record_run(started_at, sweep, result, error)
    except Exception:
        log.exception("event_association_record_failed")  # the run itself already finished
