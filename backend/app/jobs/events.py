import asyncio

import dramatiq

from app.events.execution import process_events
from app.jobs.broker import broker  # noqa: F401


@dramatiq.actor(queue_name="events", max_retries=0)
def associate_events(sweep: bool = False) -> None:
    asyncio.run(process_events(sweep))
