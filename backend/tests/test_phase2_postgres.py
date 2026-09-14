import os
import uuid

import pytest
from sqlalchemy import func, select

from app.db.session import session_factory
from app.feeds.ingestion import ingest_claim
from app.feeds.models import Article, Feed, FeedArticle, FeedFetch
from app.feeds.service import claim_feed

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
FIXTURE_PORT = os.getenv("NEWSINTEL_TEST_FIXTURE_PORT", "18080")


async def _add_feed(name: str, url: str) -> uuid.UUID:
    async with session_factory() as db:
        feed = Feed(
            name=name, url=url, tags=[], enabled=True, poll_interval_minutes=30, fetching_mode="rss"
        )
        db.add(feed)
        await db.commit()
        return feed.id


async def test_repeated_and_cross_feed_ingestion_deduplicates_but_preserves_provenance() -> None:
    first = await _add_feed("Fixture one", f"http://localhost:{FIXTURE_PORT}/feed.xml")
    second = await _add_feed(
        "Fixture two", f"http://localhost:{FIXTURE_PORT}/feed-duplicate.xml"
    )
    fetch_ids: list[uuid.UUID] = []
    for feed_id in (first, first, second):
        async with session_factory() as db:
            claimed = await claim_feed(db, feed_id)
            assert claimed is not None
            fetch, _ = claimed
            fetch_ids.append(fetch.id)
        await ingest_claim(feed_id, fetch.claim_token)
    async with session_factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Article)
                .where(Article.normalized_url == "https://news.example/story?id=1")
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(FeedArticle)
                .where(FeedArticle.feed_id.in_([first, second]))
            )
            == 2
        )
        statuses = list(
            (await db.scalars(select(FeedFetch.status).where(FeedFetch.id.in_(fetch_ids)))).all()
        )
        assert statuses == ["success", "success", "success"]


async def test_failed_feed_can_be_corrected_and_repolled() -> None:
    feed_id = await _add_feed(
        "Broken fixture", f"http://localhost:{FIXTURE_PORT}/malformed.xml"
    )
    async with session_factory() as db:
        fetch, _ = await claim_feed(db, feed_id)  # type: ignore[misc]
    await ingest_claim(feed_id, fetch.claim_token)
    async with session_factory() as db:
        failed = await db.get(FeedFetch, fetch.id)
        assert failed is not None and failed.status == "failed"
        feed = await db.get(Feed, feed_id)
        assert feed is not None
        feed.url = f"http://localhost:{FIXTURE_PORT}/feed.xml"
        await db.commit()
        recovered, _ = await claim_feed(db, feed_id)  # type: ignore[misc]
    await ingest_claim(feed_id, recovered.claim_token)
    async with session_factory() as db:
        result = await db.get(FeedFetch, recovered.id)
        assert result is not None and result.status == "success"
