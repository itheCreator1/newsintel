import asyncio
import time
import uuid
from urllib.parse import urlsplit

import dramatiq
from redis import Redis

from app.articles.processing import process_claim
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article, ArticleProcessingJob
from app.jobs.broker import broker as broker


@dramatiq.actor(max_retries=0)
def process_article(job_id: str, claim_token: str) -> None:
    settings = get_settings()

    async def hostname() -> str | None:
        async with session_factory() as db:
            job = await db.get(ArticleProcessingJob, uuid.UUID(job_id))
            article = await db.get(Article, job.article_id) if job else None
            return urlsplit(article.original_url).hostname if article else None

    host = asyncio.run(hostname())
    if not host:
        return
    redis = Redis.from_url(settings.redis_url)
    lock = redis.lock(f"newsintel:http-host:{host}", timeout=120, blocking_timeout=120)
    with lock:
        pace_key = f"newsintel:http-pace:{host}"
        while not redis.set(
            pace_key,
            "1",
            nx=True,
            px=max(1, int(settings.article_host_min_interval_seconds * 1000)),
        ):
            time.sleep(min(settings.article_host_min_interval_seconds, 0.25))
        asyncio.run(process_claim(uuid.UUID(job_id), claim_token))
