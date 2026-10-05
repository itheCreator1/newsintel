import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db.session import session_factory
from app.feeds import routes as feed_routes
from app.feeds.models import Feed

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_retiring_a_feed_clears_a_claim_a_concurrent_scheduler_is_writing() -> None:
    async with session_factory() as db:
        feed = Feed(
            name=f"Retire race {uuid.uuid4().hex[:8]}",
            url=f"http://127.0.0.1:1/{uuid.uuid4().hex}.xml",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        db.add(feed)
        await db.commit()
        feed_id = feed.id
    async with session_factory() as writer:
        # The scheduler holds the row and writes a claim it has not committed yet.
        claimed = await writer.get(Feed, feed_id, with_for_update=True)
        assert claimed is not None
        claimed.claim_token = "running"
        claimed.claim_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await writer.flush()

        async def retire() -> None:
            async with session_factory() as db:
                await feed_routes.retire_feed(feed_id, db, SimpleNamespace())  # type: ignore[arg-type]

        retiring = asyncio.create_task(retire())
        await asyncio.sleep(0.3)
        await writer.commit()
        await retiring
    async with session_factory() as db:
        stored = await db.get(Feed, feed_id)
        assert stored is not None
        assert stored.retired_at is not None and not stored.enabled
        assert stored.claim_token is None and stored.claim_expires_at is None
