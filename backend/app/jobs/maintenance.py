import asyncio
from datetime import UTC, datetime

import dramatiq

from app.jobs.broker import broker  # noqa: F401
from app.operations.retention import run_retention


@dramatiq.actor(max_retries=0)
def prune_history() -> None:
    """The history cleanup on demand (the Processes page's Run now); the scheduler runs it
    hourly."""
    asyncio.run(run_retention(datetime.now(UTC)))
