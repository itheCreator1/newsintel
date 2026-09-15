import asyncio
import uuid

import dramatiq

from app.clustering.execution import process_job
from app.jobs.broker import broker  # noqa: F401


@dramatiq.actor(queue_name="clustering", max_retries=0)
def process_clustering(job_id: str, claim_token: str) -> None:
    asyncio.run(process_job(uuid.UUID(job_id), claim_token))
