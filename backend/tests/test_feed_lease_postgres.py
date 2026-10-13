"""A feed fetch fits inside its lease (fix plan Δ2γ).

The fetch slept between its three attempts for up to five minutes each, Retry-After included, on a
five-minute lease: a slow feed outlived its claim, and the scheduler sent a second fetch beside it.
"""

import asyncio
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds import ingestion
from app.feeds.http import FeedHttpResponse
from app.feeds.models import Feed, FeedFetch
from app.feeds.service import claim_feed

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Wire</title>
<item><title>Harbour workers agree a new contract</title><link>https://wire.example.test/a</link>
</item></channel></rss>"""


async def _claimed() -> tuple[uuid.UUID, str]:
    async with session_factory() as db:
        feed = Feed(
            name=f"Lease {uuid.uuid4().hex[:8]}",
            url=f"https://wire.example.test/{uuid.uuid4().hex}.xml",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        db.add(feed)
        await db.commit()
        feed_id = feed.id
    async with session_factory() as db:
        claimed = await claim_feed(db, feed_id)
    assert claimed is not None
    return feed_id, claimed[0].claim_token


def _serve(monkeypatch: pytest.MonkeyPatch, *answers: Callable[[], Any]) -> None:
    pending = list(answers)

    async def fetch(_url: str, _validators: object, _settings: object) -> FeedHttpResponse:
        return await pending.pop(0)()

    monkeypatch.setattr(ingestion, "fetch_feed_http", fetch)


def _status(code: int, **headers: str) -> Callable[[], Any]:
    async def answer() -> FeedHttpResponse:
        body = RSS if code == 200 else b""
        return FeedHttpResponse(code, body, httpx.Headers(headers), "https://wire.example.test/")

    return answer


async def _state(feed_id: uuid.UUID, token: str) -> tuple[Feed, FeedFetch]:
    async with session_factory() as db:
        feed = await db.get(Feed, feed_id)
        fetch = await db.scalar(
            select(FeedFetch).where(FeedFetch.feed_id == feed_id, FeedFetch.claim_token == token)
        )
    assert feed is not None and fetch is not None
    return feed, fetch


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The waits the fetch asked for; none of them is slept."""
    asked: list[float] = []

    async def sleep(seconds: float) -> None:
        asked.append(seconds)

    monkeypatch.setattr(ingestion, "_sleep", sleep)
    return asked


async def test_the_feed_lease_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "feed_lease_seconds", 42)
    before = datetime.now(UTC)
    feed_id, _token = await _claimed()
    async with session_factory() as db:
        feed = await db.get(Feed, feed_id)
    assert feed is not None and feed.claim_expires_at is not None
    lease = feed.claim_expires_at - before
    assert timedelta(seconds=40) <= lease <= timedelta(seconds=45)


async def test_retry_after_reschedules_instead_of_sleeping(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    _serve(monkeypatch, _status(503, **{"Retry-After": "1200"}))
    feed_id, token = await _claimed()
    before = datetime.now(UTC)
    await ingestion.ingest_claim(feed_id, token)
    feed, fetch = await _state(feed_id, token)
    assert sleeps == []
    assert (fetch.status, fetch.error_category) == ("failed", "http_transient")
    assert (feed.claim_token, feed.claim_expires_at) == (None, None)
    # The server's own time, not the poll interval, and not a 300-second cap on it.
    assert feed.next_poll_at is not None
    assert timedelta(seconds=1195) <= feed.next_poll_at - before <= timedelta(seconds=1210)


async def test_a_short_wait_that_fits_the_lease_is_still_waited_out(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    _serve(monkeypatch, _status(503, **{"Retry-After": "3"}), _status(200))
    feed_id, token = await _claimed()
    await ingestion.ingest_claim(feed_id, token)
    _feed, fetch = await _state(feed_id, token)
    assert len(sleeps) == 1 and 3 <= sleeps[0] < 4
    assert (fetch.status, fetch.attempt_count) == ("success", 2)


async def test_a_fetch_never_runs_past_its_lease(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    async def hang() -> FeedHttpResponse:
        await asyncio.Event().wait()
        raise AssertionError("never answered")

    monkeypatch.setattr(get_settings(), "feed_lease_seconds", 4)
    _serve(monkeypatch, hang)
    feed_id, token = await _claimed()
    started = datetime.now(UTC)
    await asyncio.wait_for(ingestion.ingest_claim(feed_id, token), 30)
    feed, fetch = await _state(feed_id, token)
    assert datetime.now(UTC) - started < timedelta(seconds=4)
    assert (fetch.status, fetch.error_category) == ("failed", "timeout")
    assert feed.claim_token is None
