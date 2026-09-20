import structlog

from app.db.session import session_factory
from app.events.engine import (
    EVENT_BATCH,
    BatchResult,
    EventAssociator,
    RuleEventAssociator,
    reconcile_events,
    summary,
)

log = structlog.get_logger()

# Batches per run; the scheduler starts another run soon, so a backlog drains without one long job.
MAX_BATCHES = 20
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
        for field in ("evaluated", "created", "deleted", "failed"):
            setattr(total, field, getattr(total, field) + getattr(batch, field))
        if batch.skipped or batch.evaluated + batch.failed < EVENT_BATCH:
            break
    if sweep and not total.skipped:
        async with session_factory() as db, db.begin():
            swept = await reconcile_events(db, associator().version)
        total.deleted += swept.deleted
    return total


async def process_events(sweep: bool = False) -> None:
    try:
        result = await run_association(sweep)
    except Exception:
        log.exception("event_association_run_failed", error_category="event_association")
        return
    if result.skipped or result.evaluated or result.failed or result.deleted:
        log.info("event_association", skipped=result.skipped, sweep=sweep, **summary(result))
