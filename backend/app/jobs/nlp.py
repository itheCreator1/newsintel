import asyncio
import uuid

import dramatiq

from app.jobs.broker import broker  # noqa: F401
from app.nlp.execution import process_job


@dramatiq.actor(queue_name="nlp", max_retries=0)
def process_nlp(job_id: str, claim_token: str) -> None:
    asyncio.run(process_job(uuid.UUID(job_id), claim_token))
