import asyncio
import time
import uuid
from urllib.parse import urlsplit

import dramatiq
from redis import Redis

from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.ingestion import ingest_claim
from app.feeds.models import Feed
from app.jobs.broker import broker as broker


@dramatiq.actor(max_retries=0)
def ingest_feed(feed_id: str, claim_token: str) -> None:
    settings = get_settings()

    async def feed_hostname() -> str | None:
        async with session_factory() as db:
            feed = await db.get(Feed, uuid.UUID(feed_id))
            return urlsplit(feed.url).hostname if feed else None

    hostname = asyncio.run(feed_hostname())
    if not hostname:
        return
    redis = Redis.from_url(settings.redis_url)
    lock = redis.lock(f"newsintel:http-host:{hostname}", timeout=120, blocking_timeout=120)
    with lock:
        pace_key = f"newsintel:http-pace:{hostname}"
        while not redis.set(
            pace_key, "1", nx=True, px=max(1, int(settings.feed_host_min_interval_seconds * 1000))
        ):
            time.sleep(min(settings.feed_host_min_interval_seconds, 0.25))
        asyncio.run(ingest_claim(uuid.UUID(feed_id), claim_token))
