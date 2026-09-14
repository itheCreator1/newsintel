import asyncio

import dramatiq

from app.jobs.broker import broker  # noqa: F401
from app.search.indexing import process_delivery


@dramatiq.actor(queue_name="search")
def index_article(delivery_id: str, claim_token: str) -> None:
    asyncio.run(process_delivery(delivery_id, claim_token))
