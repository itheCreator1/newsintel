import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.session import session_factory
from app.feeds.ingestion import ingest_claim
from app.feeds.models import Feed, FeedFetch
from app.feeds.service import claim_feed

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _claimed_feed() -> tuple[uuid.UUID, FeedFetch]:
    async with session_factory() as db:
        feed = Feed(
            name=f"Fetch status {uuid.uuid4().hex[:8]}",
            # Never fetched: every path under test returns before the HTTP request.
            url=f"http://127.0.0.1:1/{uuid.uuid4().hex}.xml",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        db.add(feed)
        await db.commit()
        claimed = await claim_feed(db, feed.id)
        assert claimed is not None and claimed[1] is False
        return feed.id, claimed[0]


async def _fetch(fetch_id: uuid.UUID) -> FeedFetch:
    async with session_factory() as db:
        fetch = await db.get(FeedFetch, fetch_id)
        assert fetch is not None
        return fetch


async def test_a_fetch_whose_feed_was_retired_before_the_worker_ran_ends_failed() -> None:
    feed_id, fetch = await _claimed_feed()
    async with session_factory() as db, db.begin():
        feed = await db.get(Feed, feed_id)
        assert feed is not None
        feed.retired_at = datetime.now(UTC)
        feed.enabled = False
        feed.claim_token = feed.claim_expires_at = None

    await ingest_claim(feed_id, fetch.claim_token)

    stored = await _fetch(fetch.id)
    assert (stored.status, stored.error_category) == ("failed", "abandoned")
    assert stored.completed_at is not None


async def test_reclaiming_a_feed_after_its_lease_expired_fails_the_fetch_it_replaces() -> None:
    feed_id, first = await _claimed_feed()
    async with session_factory() as db, db.begin():
        feed = await db.get(Feed, feed_id)
        assert feed is not None
        feed.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    async with session_factory() as db:
        claimed = await claim_feed(db, feed_id)

    assert claimed is not None and claimed[1] is False and claimed[0].id != first.id
    stored = await _fetch(first.id)
    assert (stored.status, stored.error_category) == ("failed", "lease_expired")
    assert stored.completed_at is not None
    assert (await _fetch(claimed[0].id)).status == "queued"


async def test_a_redelivered_message_leaves_a_finished_fetch_alone() -> None:
    feed_id, fetch = await _claimed_feed()
    async with session_factory() as db, db.begin():
        feed = await db.get(Feed, feed_id)
        stored = await db.get(FeedFetch, fetch.id)
        assert feed is not None and stored is not None
        stored.status = "success"
        feed.claim_token = feed.claim_expires_at = None

    await ingest_claim(feed_id, fetch.claim_token)

    assert (await _fetch(fetch.id)).status == "success"
