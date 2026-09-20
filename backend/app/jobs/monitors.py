import asyncio
import uuid

import dramatiq

from app.jobs.broker import broker  # noqa: F401
from app.monitors.evaluation import process_monitor


@dramatiq.actor(queue_name="monitors", max_retries=0)
def evaluate_monitor(monitor_id: str, claim_token: str) -> None:
    asyncio.run(process_monitor(uuid.UUID(monitor_id), claim_token))
