import asyncio
import uuid

import dramatiq

from app.jobs.broker import broker  # noqa: F401
from app.wikidata.runs import run_job

# A batch stops starting work after wikidata_run_batch_seconds; a request already waiting on the
# throttle (a slow answer, then a 5 s gap) still finishes inside this limit.
TIME_LIMIT_MS = 10 * 60 * 1000


@dramatiq.actor(max_retries=0, time_limit=TIME_LIMIT_MS)
def process_wikidata_run(run_id: str, claim_token: str) -> None:
    asyncio.run(run_job(uuid.UUID(run_id), uuid.UUID(claim_token)))
