import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db.session import session_factory
from app.feeds import routes as feed_routes
from app.feeds.models import Feed, FeedFetch

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


async def _feed(db, tag: str, name: str, **values) -> Feed:  # type: ignore[no-untyped-def]
    feed = Feed(
        name=name,
        url=f"http://127.0.0.1:1/{uuid.uuid4().hex}.xml",
        tags=[tag, *values.pop("tags", [])],
        enabled=values.pop("enabled", True),
        poll_interval_minutes=30,
        fetching_mode="rss",
        **values,
    )
    db.add(feed)
    await db.flush()
    return feed


async def _list(db, **params):  # type: ignore[no-untyped-def]
    defaults = {"cursor": None, "limit": 30, "q": None, "status": None, "tag": None, "sort": "newest"}
    return await feed_routes.list_feeds(db, SimpleNamespace(), **{**defaults, **params})  # type: ignore[arg-type]


async def test_feed_list_finds_by_name_or_url_and_by_tag() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        wire = await _feed(db, tag, "Harbor Wire 100%")
        daily = await _feed(db, tag, "Harbor Daily", tags=["economy"])
        await db.commit()
        assert {item.id for item in (await _list(db, tag=tag)).items} == {wire.id, daily.id}
        assert [item.id for item in (await _list(db, tag=tag, q="wire")).items] == [wire.id]
        assert [item.id for item in (await _list(db, tag=tag, q="100%")).items] == [wire.id]
        assert [item.id for item in (await _list(db, tag=tag, q=daily.url[-20:])).items] == [daily.id]
        # The wildcard characters are literal, so "_" does not match any one character.
        assert (await _list(db, tag=tag, q="Harbor_Wire")).items == []


async def test_feed_list_filters_by_state_and_reports_the_last_fetch() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with session_factory() as db:
        healthy = await _feed(db, tag, "Healthy")
        failing = await _feed(db, tag, "Failing")
        recovered = await _feed(db, tag, "Recovered")
        disabled = await _feed(db, tag, "Disabled", enabled=False)
        retired = await _feed(db, tag, "Retired", enabled=False, retired_at=now)
        for feed, outcomes in (
            (healthy, ["success"]),
            (failing, ["success", "failed"]),
            (recovered, ["failed", "success"]),
        ):
            for index, outcome in enumerate(outcomes):
                db.add(
                    FeedFetch(
                        feed_id=feed.id,
                        claim_token=uuid.uuid4().hex,
                        status=outcome,
                        error_category="http" if outcome == "failed" else None,
                        error_message="HTTP 500" if outcome == "failed" else None,
                        started_at=now - timedelta(minutes=10 - index),
                        completed_at=now - timedelta(minutes=9 - index),
                    )
                )
        await db.commit()

        everything = await _list(db, tag=tag)
        assert {item.id for item in everything.items} == {
            healthy.id, failing.id, recovered.id, disabled.id,
        }
        by_id = {item.id: item for item in everything.items}
        assert by_id[failing.id].last_fetch_status == "failed"
        assert by_id[failing.id].last_fetch_error == "http: HTTP 500"
        assert by_id[recovered.id].last_fetch_status == "success"
        assert by_id[recovered.id].last_fetch_error is None
        assert by_id[disabled.id].last_fetch_status is None

        assert [item.id for item in (await _list(db, tag=tag, status="failing")).items] == [failing.id]
        assert [item.id for item in (await _list(db, tag=tag, status="disabled")).items] == [disabled.id]
        assert {item.id for item in (await _list(db, tag=tag, status="active")).items} == {
            healthy.id, failing.id, recovered.id,
        }
        retired_page = await _list(db, tag=tag, status="retired")
        assert [item.id for item in retired_page.items] == [retired.id]
        assert retired_page.items[0].retired_at is not None


async def test_feed_list_sorts_by_name_across_pages() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        for name in ("beta", "Alpha", "gamma", "Delta"):
            await _feed(db, tag, name)
        await db.commit()
        first = await _list(db, tag=tag, sort="name", limit=2)
        assert [item.name for item in first.items] == ["Alpha", "beta"]
        assert first.next_cursor
        second = await _list(db, tag=tag, sort="name", limit=2, cursor=first.next_cursor)
        assert [item.name for item in second.items] == ["Delta", "gamma"]
        assert second.next_cursor is None


async def test_feed_tags_count_the_sources_that_carry_them() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        await _feed(db, tag, "One", tags=[f"{tag}-extra"])
        await _feed(db, tag, "Two")
        await _feed(db, tag, "Gone", enabled=False, retired_at=datetime.now(UTC))
        await db.commit()
        counts = {item.tag: item.count for item in await feed_routes.list_feed_tags(db, SimpleNamespace())}  # type: ignore[arg-type]
        assert counts[tag] == 2
        assert counts[f"{tag}-extra"] == 1
