import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.db.session import session_factory
from app.feeds import routes as feed_routes
from app.feeds.discovery import FeedProbe, FeedProbeError
from app.feeds.models import Feed, FeedFetch
from app.feeds.opml import OpmlEntry, parse_opml, render_opml
from app.feeds.schemas import FeedCreate, FeedImport, FeedTestRequest, OpmlPreviewRequest
from app.search.criteria import search_criteria

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
    defaults = {
        "cursor": None,
        "limit": 30,
        "q": None,
        "status": None,
        "tag": None,
        "sort": "newest",
    }
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
        assert [item.id for item in (await _list(db, tag=tag, q=daily.url[-20:])).items] == [
            daily.id
        ]
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
            healthy.id,
            failing.id,
            recovered.id,
            disabled.id,
        }
        by_id = {item.id: item for item in everything.items}
        assert by_id[failing.id].last_fetch_status == "failed"
        assert by_id[failing.id].last_fetch_error == "http: HTTP 500"
        assert by_id[recovered.id].last_fetch_status == "success"
        assert by_id[recovered.id].last_fetch_error is None
        assert by_id[disabled.id].last_fetch_status is None

        assert [item.id for item in (await _list(db, tag=tag, status="failing")).items] == [
            failing.id
        ]
        assert [item.id for item in (await _list(db, tag=tag, status="disabled")).items] == [
            disabled.id
        ]
        assert {item.id for item in (await _list(db, tag=tag, status="active")).items} == {
            healthy.id,
            failing.id,
            recovered.id,
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
        counts = {
            item.tag: item.count for item in await feed_routes.list_feed_tags(db, SimpleNamespace())
        }  # type: ignore[arg-type]
        assert counts[tag] == 2
        assert counts[f"{tag}-extra"] == 1


async def test_opml_export_lists_the_sources_that_are_not_retired() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        kept = await _feed(db, tag, "Kept", source_country="GR", expected_language="el")
        await _feed(db, tag, "Gone", enabled=False, retired_at=datetime.now(UTC))
        await db.commit()
        response = await feed_routes.export_opml(db, SimpleNamespace())  # type: ignore[arg-type]
    assert response.media_type == "text/x-opml"
    assert "newsintel-sources.opml" in response.headers["content-disposition"]
    entries = {entry.url: entry for entry in parse_opml(response.body).entries}
    assert entries[kept.url] == OpmlEntry("Kept", kept.url, [tag], "GR", "el")
    assert not any(entry.name == "Gone" and tag in entry.tags for entry in entries.values())


async def test_opml_preview_marks_known_addresses_and_import_adds_only_new_ones() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        known = await _feed(db, tag, "Known")
        await db.commit()
        fresh = f"https://fresh.example/{uuid.uuid4().hex}.xml"
        document = render_opml(
            [
                OpmlEntry("Known again", known.url, [tag], None, None),
                OpmlEntry("Fresh", fresh, [tag, "imported"], "GR", "el"),
            ],
            title="t",
        )
        preview = await feed_routes.preview_opml(
            OpmlPreviewRequest(opml=document.decode()),
            db,
            SimpleNamespace(),  # type: ignore[arg-type]
        )
        assert [(item.name, item.existing_feed_id) for item in preview.items] == [
            ("Known again", known.id),
            ("Fresh", None),
        ]
        assert preview.invalid == 0

        result = await feed_routes.import_feeds(
            FeedImport(
                feeds=[
                    FeedCreate(
                        name=item.name,
                        url=item.url,
                        tags=item.tags,
                        source_country=item.source_country,
                        expected_language=item.expected_language,
                    )
                    for item in preview.items
                ]
            ),
            db,
            SimpleNamespace(),  # type: ignore[arg-type]
        )
        assert (result.created, result.skipped) == (1, 1)
        page = await _list(db, tag=tag, sort="name")
        assert [(item.name, item.source_country, item.tags) for item in page.items] == [
            ("Fresh", "GR", [tag, "imported"]),
            ("Known", None, [tag]),
        ]


async def test_opml_preview_says_when_a_file_is_not_opml() -> None:
    async with session_factory() as db:
        with pytest.raises(HTTPException) as raised:
            await feed_routes.preview_opml(
                OpmlPreviewRequest(opml="<rss/>"),
                db,
                SimpleNamespace(),  # type: ignore[arg-type]
            )
    assert raised.value.status_code == 422


async def test_testing_a_feed_says_whether_it_is_already_a_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        known = await _feed(db, tag, "Known")
        await db.commit()

        async def probe(url: str, _fetch: object) -> FeedProbe:
            return FeedProbe(url, "Title", 3, "en", None, None, [])

        monkeypatch.setattr(feed_routes, "probe_feed", probe)
        result = await feed_routes.test_feed_url(
            FeedTestRequest(url=known.url),
            db,
            SimpleNamespace(),  # type: ignore[arg-type]
        )
        assert (result.title, result.entry_count, result.existing_feed_id) == ("Title", 3, known.id)

        async def broken(url: str, _fetch: object) -> FeedProbe:
            raise FeedProbeError("The address answered HTTP 404")

        monkeypatch.setattr(feed_routes, "probe_feed", broken)
        with pytest.raises(HTTPException) as raised:
            await feed_routes.test_feed_url(
                FeedTestRequest(url="https://x.example/"),
                db,
                SimpleNamespace(),  # type: ignore[arg-type]
            )
        assert (raised.value.status_code, raised.value.detail) == (
            422,
            "The address answered HTTP 404",
        )


async def test_a_source_tag_in_search_criteria_resolves_to_the_sources_carrying_it() -> None:
    tag = f"tag-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        one = await _feed(db, tag, "One")
        two = await _feed(db, f"{tag}-other", "Two", tags=[f"{tag}-third"])
        await _feed(db, f"{tag}-unpicked", "Three")
        await db.commit()
        criteria = await search_criteria(db, source_tag=[tag, f"{tag}-third"])
        assert criteria.source_tags == sorted([tag, f"{tag}-third"])
        assert criteria.tag_sources == sorted([str(one.id), str(two.id)])
        assert criteria.fingerprint()["source_tags"] == criteria.source_tags
        assert not criteria.empty
        # Without a tag, the fingerprint is the one saved monitors already hold.
        assert "source_tags" not in (await search_criteria(db, q="grid")).fingerprint()
