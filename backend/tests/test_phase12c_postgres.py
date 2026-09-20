import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from event_fixtures import BASE, entities, feed, story
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.auth.models import Session
from app.auth.routes import current_session
from app.clustering.models import StoryCluster
from app.db.session import session_factory
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event
from app.events.service import associate_cluster, create_event, refresh_event, set_status
from app.main import create_app

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _version() -> str:
    """A version of its own, so tests never see each other's events."""
    return f"t-{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def _client(signed_in: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    if signed_in:
        login = Session(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            csrf_token="csrf-token",
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        app.dependency_overrides[current_session] = lambda: login
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _get(path: str, **params: Any) -> dict[str, Any]:
    async with _client() as client:
        response = await client.get(f"/api/v1{path}", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _make(db, version: str, clusters: list[StoryCluster], status: str = "active") -> Event:  # type: ignore[no-untyped-def]
    event = await create_event(db, version)
    for cluster in clusters:
        await associate_cluster(db, event, cluster.id, 0.8, {"entities": 0.5})
    await refresh_event(db, event)
    if status != "active":
        await set_status(db, event, status)
    return event


async def _walk(path: str, limit: int, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(50):
        page = await _get(path, limit=limit, **params, **({"cursor": cursor} if cursor else {}))
        assert len(page["items"]) <= limit
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            return items
    raise AssertionError("pagination did not end")


async def test_the_list_is_newest_first_and_pages_do_not_skip_or_repeat_on_ties() -> None:
    version = _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 3)
        made = []
        for hours in (0, 0, 0, 5, 5, 9, 1):  # several events share an end time
            made.append(await _make(db, version, [await story(db, ents, hours=hours)]))
        ids = {str(e.id) for e in made}
    every = await _walk("/events", 100, algorithm_version=version)
    for size in (1, 2, 3):
        paged = await _walk("/events", size, algorithm_version=version)
        assert [e["id"] for e in paged] == [e["id"] for e in every]
    assert {e["id"] for e in every} == ids and len(every) == 7
    keys = [(e["ended_at"], e["id"]) for e in every]
    assert keys == sorted(keys, reverse=True)


async def test_filters_apply_alone_and_together() -> None:
    version = _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 4)
        gr = await _make(db, version, [await story(db, ents[:2], hours=0, country="GR")])
        tr = await _make(db, version, [await story(db, ents[1:3], hours=30, country="TR")])
        closed = await _make(
            db, version, [await story(db, ents[:2], hours=60, country="GR")], status="closed"
        )
        shared, other = ents[1].id, ents[3].id
        ids = {name: str(e.id) for name, e in (("gr", gr), ("tr", tr), ("closed", closed))}

    async def found(**params: Any) -> set[str]:
        page = await _get("/events", algorithm_version=version, **params)
        return {e["id"] for e in page["items"]}

    assert await found() == set(ids.values())
    assert await found(status="closed") == {ids["closed"]}
    assert await found(country="tr") == {ids["tr"]}
    assert await found(entity_id=str(shared)) == set(ids.values())
    assert await found(entity_id=str(other)) == set()
    # A span overlaps when it ends on or after `from` and starts on or before `to`.
    assert await found(**{"from": (BASE + timedelta(hours=40)).isoformat()}) == {ids["closed"]}
    assert await found(to=(BASE + timedelta(hours=10)).isoformat()) == {ids["gr"]}
    window = {
        "from": (BASE + timedelta(hours=20)).isoformat(),
        "to": (BASE + timedelta(hours=40)).isoformat(),
    }
    assert await found(**window) == {ids["tr"]}
    assert await found(country="GR", status="active", entity_id=str(shared)) == {ids["gr"]}
    assert await found(status="superseded") == set()


async def test_the_default_version_is_the_current_one_and_others_are_opt_in() -> None:
    other = _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        current = await _make(
            db, EVENT_ALGORITHM_VERSION, [await story(db, ents, hours=1, country="GR")]
        )
        older = await _make(db, other, [await story(db, ents, hours=2, country="GR")])
        current_id, older_id, shared = str(current.id), str(older.id), str(ents[0].id)
    default = await _walk("/events", 100, entity_id=shared)
    assert [e["id"] for e in default] == [current_id]
    assert default[0]["algorithm_version"] == EVENT_ALGORITHM_VERSION
    explicit = await _walk("/events", 100, entity_id=shared, algorithm_version=other)
    assert [e["id"] for e in explicit] == [older_id]


async def test_bad_input_and_missing_events_are_rejected_on_every_route() -> None:
    async with session_factory() as db, db.begin():
        event = await _make(db, _version(), [await story(db, await entities(db, 2))])
        known = str(event.id)
    async with _client() as client:
        for suffix in ("", "/clusters", "/articles", "/timeline"):
            missing = await client.get(f"/api/v1/events/{uuid.uuid4()}{suffix}")
            assert missing.status_code == 404, suffix
            malformed = await client.get(f"/api/v1/events/not-a-uuid{suffix}")
            assert malformed.status_code == 422, suffix
        for path in ("/events", f"/events/{known}/clusters", f"/events/{known}/articles"):
            assert (await client.get(f"/api/v1{path}", params={"cursor": "bad"})).status_code == 400
        for path in ("/events", f"/events/{known}/clusters", f"/events/{known}/timeline"):
            for limit in (0, 101):
                response = await client.get(f"/api/v1{path}", params={"limit": limit})
                assert response.status_code == 422, (path, limit)
        assert (
            await client.get(f"/api/v1/events/{known}/timeline", params={"after": "yesterday"})
        ).status_code == 422
        assert (await client.get("/api/v1/events", params={"status": "bogus"})).status_code == 422
        assert (await client.get("/api/v1/events", params={"country": "GRC"})).status_code == 422


async def test_every_route_needs_a_session() -> None:
    known = uuid.uuid4()
    async with _client(signed_in=False) as client:
        for path in (
            "",
            f"/{known}",
            f"/{known}/clusters",
            f"/{known}/articles",
            f"/{known}/timeline",
        ):
            response = await client.get(f"/api/v1/events{path}")
            assert response.status_code == 401, path


async def test_counts_are_distinct_articles_and_sources() -> None:
    version = _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        shared, only = await feed(db), await feed(db)
        one = await story(db, ents, hours=0, articles=2, feeds=[shared, only])
        two = await story(db, ents, hours=1, articles=3, feeds=[shared])
        event = await _make(db, version, [one, two])
        event_id = str(event.id)
    summary = (await _get("/events", algorithm_version=version))["items"][0]
    assert summary["id"] == event_id
    assert (summary["cluster_count"], summary["article_count"], summary["source_count"]) == (
        2,
        5,
        2,
    )
    detail = await _get(f"/events/{event_id}")
    assert (detail["cluster_count"], detail["article_count"], detail["source_count"]) == (2, 5, 2)


async def test_the_headline_is_the_largest_clusters_representative_then_the_earliest() -> None:
    version = _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        small = await story(db, ents, title="Small story", hours=0, articles=2)
        large = await story(db, ents, title="Large story", hours=2, articles=4)
        tie_early = await story(db, ents, title="Early tie", hours=1, articles=2)
        largest = await _make(db, version, [small, large, tie_early])
        tie_late = await story(db, ents, title="Late tie", hours=4, articles=2)
        tie_first = await story(db, ents, title="First tie", hours=3, articles=2)
        tied = await _make(db, version, [tie_late, tie_first])
        largest_id, tied_id = str(largest.id), str(tied.id)
        large_article = str(large.representative_article_id)
    by_id = {e["id"]: e for e in (await _get("/events", algorithm_version=version))["items"]}
    assert by_id[largest_id]["headline"] == "Large story"
    assert by_id[largest_id]["headline_article_id"] == large_article
    # Equal sizes: the cluster that started first supplies the headline.
    assert by_id[tied_id]["headline"] == "First tie"


async def test_an_event_without_a_representative_has_no_headline() -> None:
    version = _version()
    async with session_factory() as db, db.begin():
        cluster = await story(db, await entities(db, 2))
        cluster.representative_article_id = None
        event = await _make(db, version, [cluster])
        event_id = str(event.id)
    detail = await _get(f"/events/{event_id}")
    assert detail["headline"] is None and detail["headline_article_id"] is None
    assert detail["article_count"] == 2


async def test_entities_are_ranked_and_capped_and_the_detail_carries_timestamps() -> None:
    version = _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 22)
        rare = await entities(db, 1)
        common = await story(db, ents, hours=0, articles=3)
        extra = await story(db, [*ents[:3], *rare], hours=1, articles=2)
        event = await _make(db, version, [common, extra])
        event_id, rare_id = str(event.id), str(rare[0].id)
    detail = await _get(f"/events/{event_id}")
    listed = (await _get("/events", algorithm_version=version))["items"][0]
    assert len(detail["entities"]) == 20 and len(listed["entities"]) == 5
    assert [e["id"] for e in detail["entities"][:5]] == [e["id"] for e in listed["entities"]]
    counts = [e["article_count"] for e in detail["entities"]]
    assert counts == sorted(counts, reverse=True) and counts[0] == 5
    assert rare_id not in {e["id"] for e in detail["entities"]}  # 2 articles: below the cap
    assert {"created_at", "updated_at"} <= set(detail)
    assert detail == await _get(f"/events/{event_id}")  # deterministic


async def test_clusters_carry_the_stored_reason_and_page_newest_first() -> None:
    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        clusters = [await story(db, ents, title=f"Story {h}", hours=h) for h in (0, 4, 8)]
        event = await _make(db, _version(), clusters)
        event_id, newest = str(event.id), str(clusters[2].id)
        newest_article = str(clusters[2].representative_article_id)
    everything = await _walk(f"/events/{event_id}/clusters", 100)
    paged = await _walk(f"/events/{event_id}/clusters", 1)
    assert [c["id"] for c in paged] == [c["id"] for c in everything]
    assert everything[0]["id"] == newest
    assert [c["representative_article"]["title"] for c in everything] == [
        "Story 8",
        "Story 4",
        "Story 0",
    ]
    first = everything[0]
    assert first["representative_article"]["id"] == newest_article
    assert first["score"] == 0.8 and first["signals"] == {"entities": 0.5}
    assert first["joined_at"] and first["article_count"] == 2


async def test_articles_page_newest_first_with_their_cluster() -> None:
    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        early = await story(db, ents, title="Early", hours=0, articles=2)
        late = await story(db, ents, title="Late", hours=3, articles=3)
        event = await _make(db, _version(), [early, late])
        event_id, early_id, late_id = str(event.id), str(early.id), str(late.id)
    everything = await _walk(f"/events/{event_id}/articles", 100)
    assert [a["title"] for a in everything] == ["Late"] * 3 + ["Early"] * 2
    assert [a["cluster_id"] for a in everything] == [late_id] * 3 + [early_id] * 2
    ordered = [(a["published_at"], a["id"]) for a in everything]
    assert ordered == sorted(ordered, reverse=True)
    for size in (1, 2):
        paged = await _walk(f"/events/{event_id}/articles", size)
        assert [a["id"] for a in paged] == [a["id"] for a in everything]
    assert everything[0]["provenance"] == []


async def test_the_timeline_buckets_by_utc_day_and_pages_by_date() -> None:
    midnight = (BASE + timedelta(days=1)).replace(hour=0)

    def hours_to(moment: datetime) -> float:
        return (moment - BASE) / timedelta(hours=1)

    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        a, b = await feed(db), await feed(db)
        before = await story(
            db, ents, title="Before", hours=hours_to(midnight - timedelta(minutes=30)), feeds=[a]
        )
        after = await story(
            db, ents, title="After", hours=hours_to(midnight + timedelta(minutes=30)), feeds=[a, b]
        )
        later = await story(
            db, ents, title="Later", hours=hours_to(midnight + timedelta(hours=2)), articles=4
        )
        event = await _make(db, _version(), [before, after, later])
        event_id = str(event.id)
    day_before = (midnight - timedelta(days=1)).date()
    day_after = midnight.date()

    every = (await _get(f"/events/{event_id}/timeline"))["items"]
    assert [d["date"] for d in every] == [day_before.isoformat(), day_after.isoformat()]
    first, second = every
    assert (first["article_count"], first["source_count"], first["clusters_started"]) == (2, 1, 1)
    assert (second["article_count"], second["source_count"], second["clusters_started"]) == (
        6,
        2,
        2,
    )
    assert [e["title"] for e in first["evidence"]] == ["Before", "Before"]
    # Three earliest articles that day: both of "After", then the first of "Later".
    assert [e["title"] for e in second["evidence"]] == ["After", "After", "Later"]

    page = await _get(f"/events/{event_id}/timeline", limit=1)
    assert [d["date"] for d in page["items"]] == [day_before.isoformat()]
    assert page["next_cursor"] == day_before.isoformat()
    rest = await _get(f"/events/{event_id}/timeline", limit=1, after=page["next_cursor"])
    assert [d["date"] for d in rest["items"]] == [day_after.isoformat()]
    assert rest["next_cursor"] is None
    assert (await _get(f"/events/{event_id}/timeline", after=day_after.isoformat()))["items"] == []
    assert date.fromisoformat(every[0]["date"]) < date.fromisoformat(every[1]["date"])


async def _statements(path: str, **params: Any) -> int:
    count = 0

    def counter(*_args: Any) -> None:
        nonlocal count
        count += 1

    sa_event.listen(Engine, "before_cursor_execute", counter)
    try:
        await _get(path, **params)
    finally:
        sa_event.remove(Engine, "before_cursor_execute", counter)
    return count


async def test_a_list_page_costs_the_same_number_of_queries_for_3_or_30_events() -> None:
    small, large = _version(), _version()
    async with session_factory() as db, db.begin():
        ents = await entities(db, 2)
        for version, total in ((small, 3), (large, 30)):
            for hours in range(total):
                await _make(db, version, [await story(db, ents, hours=hours)])
    few = await _statements("/events", algorithm_version=small)
    many = await _statements("/events", algorithm_version=large)
    assert len((await _get("/events", algorithm_version=large))["items"]) == 30
    assert few == many


async def test_the_default_list_query_is_index_backed() -> None:
    query = (
        "EXPLAIN SELECT id FROM events WHERE algorithm_version = 'rule-1' AND ended_at IS NOT NULL"
        " ORDER BY ended_at DESC, id DESC LIMIT 31"
    )
    async with session_factory() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in await db.execute(text(query)))
        await db.rollback()
    assert "ix_events_" in plan, plan
