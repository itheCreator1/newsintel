import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from event_fixtures import BASE, annotate, entities, feed
from sqlalchemy import event as sa_event
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from app.auth.models import Session
from app.auth.routes import current_session
from app.clustering.models import StoryCluster, StoryClusterMember
from app.db.session import session_factory
from app.feeds.models import (
    Article,
    ArticleContent,
    ArticleProcessingJob,
    Feed,
    FeedArticle,
    FeedFetch,
)
from app.main import create_app
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, ArticleLanguageAnnotation

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
NOW = datetime.now(UTC)


@asynccontextmanager
async def _client(signed_in: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    if signed_in:
        login = Session(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            csrf_token="csrf-token",
            token_hash=uuid.uuid4().hex,
            expires_at=NOW + timedelta(hours=1),
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


async def _status(path: str, signed_in: bool = True, **params: Any) -> int:
    async with _client(signed_in) as client:
        return (await client.get(f"/api/v1{path}", params=params)).status_code


async def _walk(path: str, limit: int, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(50):
        page = await _get(path, limit=limit, **params, **({"cursor": cursor} if cursor else {}))
        assert len(page["items"]) <= limit
        items += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("did not terminate")


async def _article(  # type: ignore[no-untyped-def]
    db, source: Feed, at: datetime, *, published: bool = True, article_id=None
) -> Article:
    article = Article(
        id=article_id or uuid.uuid4(),
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title=f"Article {uuid.uuid4().hex[:6]}",
        normalized_title_hash=uuid.uuid4().hex,
        published_at=at if published else None,
        first_discovered_at=at,
    )
    db.add(article)
    await db.flush()
    await _carry(db, source, article, at)
    return article


async def _carry(db, source: Feed, article: Article, at: datetime) -> None:  # type: ignore[no-untyped-def]
    db.add(
        FeedArticle(
            feed_id=source.id,
            article_id=article.id,
            guid=uuid.uuid4().hex,
            feed_title=article.title,
            feed_url=article.original_url,
            metadata_json={},
            discovered_at=at,
        )
    )
    await db.flush()


async def _fetch(  # type: ignore[no-untyped-def]
    db, source: Feed, at: datetime, status: str, *, category=None, new=0, duration=None
) -> None:
    db.add(
        FeedFetch(
            feed_id=source.id,
            claim_token=uuid.uuid4().hex,
            status=status,
            error_category=category,
            new_article_count=new,
            duration_ms=duration,
            started_at=at,
        )
    )
    await db.flush()


async def _cluster(  # type: ignore[no-untyped-def]
    db, members: list[Article], sources: int
) -> StoryCluster:
    cluster = StoryCluster(
        algorithm_version="rule-1",
        article_count=len(members),
        source_count=sources,
        first_published_at=min(a.first_discovered_at for a in members),
        last_published_at=max(a.first_discovered_at for a in members),
        representative_article_id=members[0].id,
    )
    db.add(cluster)
    await db.flush()
    for member in members:
        db.add(
            StoryClusterMember(
                article_id=member.id, cluster_id=cluster.id, score=1, algorithm_version="rule-1"
            )
        )
    await db.flush()
    return cluster


async def test_the_detail_reports_derived_metrics_with_their_denominators() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        source.fetching_mode = "full_text"
        day = BASE.replace(hour=12)
        extracted = await _article(db, source, day)
        failed = await _article(db, source, day + timedelta(hours=1))
        active = await _article(db, source, day + timedelta(hours=2), published=False)
        await _article(db, source, day - timedelta(days=1), published=False)
        old = await _article(db, source, NOW - timedelta(days=40))
        db.add(
            ArticleContent(
                article_id=extracted.id,
                text="body",
                content_hash="a" * 64,
                extractor_name="t",
                extractor_version="1",
                extracted_at=day,
                last_content_change_at=day,
            )
        )
        db.add(
            ArticleProcessingJob(article_id=failed.id, requested_mode="full_text", status="failed")
        )
        db.add(
            ArticleProcessingJob(
                article_id=active.id, requested_mode="full_text", status="retrying"
            )
        )
        db.add(ArticleProcessingJob(article_id=old.id, requested_mode="full_text", status="failed"))
        await _fetch(db, source, NOW - timedelta(days=40), "failed", category="network")
        await _fetch(db, source, NOW - timedelta(hours=5), "success", new=5, duration=100)
        await _fetch(
            db, source, NOW - timedelta(hours=4), "failed", category="timeout", duration=300
        )
        await _fetch(db, source, NOW - timedelta(hours=3), "failed", category="http_transient")
        source_id = source.id

    body = await _get(f"/sources/{source_id}", days=7)
    assert body["id"] == str(source_id) and body["retired_at"] is None
    assert body["fetching_mode"] == "full_text" and body["window_days"] == 7
    assert body["publishing"] == {"articles": 4, "with_published_at": 2}
    assert body["extraction"] == {
        "articles": 4,
        "extracted": 1,
        "failed": 1,
        "in_progress": 1,
        "not_extracted": 1,
    }
    assert body["fetches"] == {
        "total": 3,
        "success": 1,
        "failed": 2,
        "new_articles": 5,
        "mean_duration_ms": 200.0,
        "failures_by_category": {"timeout": 1, "http_transient": 1},
    }
    assert body["health"]["consecutive_failures"] == 2
    assert body["health"]["last_attempt_status"] == "failed"
    assert body["health"]["last_failure"]["error_category"] == "http_transient"
    # First and last seen span the source's whole history, not the window.
    assert body["first_seen_at"].startswith((NOW - timedelta(days=40)).date().isoformat())
    assert body["last_seen_at"].startswith((day + timedelta(hours=2)).date().isoformat())
    timeline = body["timeline"]
    assert len(timeline) == 7 and timeline[-1]["date"] == NOW.date().isoformat()
    by_day = {item["date"]: item["article_count"] for item in timeline}
    assert (
        by_day[day.date().isoformat()] == 3
        and by_day[(day - timedelta(days=1)).date().isoformat()] == 1
    )
    assert sum(by_day.values()) == 4


async def test_a_source_with_no_history_has_zero_counts_and_no_failures() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        source_id = source.id
    body = await _get(f"/sources/{source_id}")
    assert body["publishing"] == {"articles": 0, "with_published_at": 0}
    assert body["extraction"]["not_extracted"] == 0 and body["fetches"]["total"] == 0
    assert body["fetches"]["mean_duration_ms"] is None
    assert body["first_seen_at"] is None and body["last_seen_at"] is None
    assert body["health"] == {
        "last_attempt_at": None,
        "last_attempt_status": None,
        "last_failure": None,
        "consecutive_failures": 0,
    }
    coverage = await _get(f"/sources/{source_id}/coverage")
    assert coverage["articles"] == 0 and coverage["entities"] == [] and coverage["languages"] == []
    timing = await _get(f"/sources/{source_id}/timing")
    assert timing == {
        "window_days": 30,
        "stories": 0,
        "first": 0,
        "median_minutes_behind": None,
        "p90_minutes_behind": None,
    }


async def test_a_retired_source_stays_viewable() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        source.enabled = False
        source.retired_at = NOW
        await _fetch(db, source, NOW - timedelta(hours=1), "success")
        source_id = source.id
    assert (await _get(f"/sources/{source_id}"))["retired_at"] is not None
    assert len((await _get(f"/sources/{source_id}/fetches"))["items"]) == 1


async def test_unknown_malformed_and_unauthorised_requests_are_refused() -> None:
    missing = uuid.uuid4()
    for suffix in ("", "/coverage", "/timing", "/articles", "/clusters", "/fetches"):
        assert await _status(f"/sources/{missing}{suffix}") == 404
        assert await _status(f"/sources/not-a-uuid{suffix}") == 422
        assert await _status(f"/sources/{missing}{suffix}", signed_in=False) == 401
    async with session_factory() as db, db.begin():
        source_id = (await feed(db)).id
    for days in (0, 367):
        assert await _status(f"/sources/{source_id}", days=days) == 422
    for suffix in ("/articles", "/clusters", "/fetches"):
        assert await _status(f"/sources/{source_id}{suffix}", cursor="nonsense") == 400
        assert await _status(f"/sources/{source_id}{suffix}", limit=0) == 422
        assert await _status(f"/sources/{source_id}{suffix}", limit=101) == 422


async def test_coverage_counts_current_annotations_and_keeps_country_roles_apart() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        other = await feed(db)
        ents = await entities(db, 2)
        first = await _article(db, source, BASE)
        second = await _article(db, source, BASE + timedelta(hours=1))
        elsewhere = await _article(db, other, BASE)
        await annotate(db, first.id, ents, "GR")
        await annotate(db, second.id, ents[:1], "GR")
        await annotate(db, elsewhere.id, ents, "US")
        stale = await db.scalar(
            select(ArticleEntity).where(
                ArticleEntity.article_id == first.id, ArticleEntity.entity_id == ents[1].id
            )
        )
        assert stale is not None
        stale.is_current = False
        run_id = stale.run_id
        db.add(
            ArticleCountryAnnotation(
                article_id=first.id,
                run_id=run_id,
                country_code="FR",
                role="mentioned",
                rule_version="t",
                occurrence_count=1,
                occurrences=[],
                input_fingerprint="a" * 64,
                is_current=True,
            )
        )
        for article, language in ((first, "en"), (second, "en")):
            db.add(
                ArticleLanguageAnnotation(
                    article_id=article.id,
                    run_id=run_id,
                    language=language,
                    confidence=0.9,
                    margin=0.5,
                    input_fingerprint="a" * 64,
                    is_current=True,
                )
            )
        source_id, top, second_entity = source.id, ents[0].id, ents[1].id

    body = await _get(f"/sources/{source_id}/coverage", days=7)
    assert body["articles"] == 2
    counts = {item["id"]: item["article_count"] for item in body["entities"]}
    assert counts == {str(top): 2}, "another source's articles and stale annotations are excluded"
    assert str(second_entity) not in counts
    assert {(c["country_code"], c["role"], c["article_count"]) for c in body["countries"]} == {
        ("GR", "primary", 2),
        ("FR", "mentioned", 1),
    }
    assert body["languages"] == [{"language": "en", "article_count": 2}]


async def _timing_fixture() -> uuid.UUID:
    """Sources A (the subject), B and C, with stories whose order is known to the minute."""
    async with session_factory() as db, db.begin():
        a, b, c = await feed(db), await feed(db), await feed(db)
        t = BASE.replace(hour=6)

        def at(minutes: int) -> datetime:
            return t + timedelta(minutes=minutes)

        await _cluster(
            db, [await _article(db, a, at(0)), await _article(db, b, at(30))], 2
        )  # first
        await _cluster(db, [await _article(db, b, at(0)), await _article(db, a, at(60))], 2)  # +60
        await _cluster(
            db, [await _article(db, b, at(0)), await _article(db, a, at(120))], 2
        )  # +120
        shared = await _article(db, a, at(0))
        await _carry(db, b, shared, at(0))  # both feeds carry the earliest article
        await _cluster(db, [shared, await _article(db, c, at(45))], 3)  # first (shared counts)
        await _cluster(db, [await _article(db, a, at(0))], 1)  # only A: not a comparison
        base = (
            uuid.uuid4().int >> 4 << 4
        )  # fresh ids per call; only the order inside a pair matters
        low, high = uuid.UUID(int=base | 1), uuid.UUID(int=base | 2)
        await _cluster(
            db,
            [
                await _article(db, a, at(0), article_id=low),
                await _article(db, b, at(0), article_id=high),
            ],
            2,
        )  # tie, lower id: first
        await _cluster(
            db,
            [
                await _article(db, b, at(0), article_id=uuid.UUID(int=base | 3)),
                await _article(db, a, at(0), article_id=uuid.UUID(int=base | 4)),
            ],
            2,
        )  # tie, higher id: behind by 0
        old = NOW - timedelta(days=40)
        await _cluster(
            db, [await _article(db, b, old), await _article(db, a, old + timedelta(hours=1))], 2
        )
        return a.id


async def test_timing_counts_only_shared_stories_in_the_window_and_breaks_ties_by_id() -> None:
    source_id = await _timing_fixture()
    timing = await _get(f"/sources/{source_id}/timing", days=7)
    assert timing["stories"] == 6 and timing["first"] == 3
    assert timing["median_minutes_behind"] == 60.0  # behind by [0, 60, 120]
    assert timing["p90_minutes_behind"] == pytest.approx(108.0)
    wide = await _get(f"/sources/{source_id}/timing", days=60)
    assert wide["stories"] == 7  # the 40-day-old story enters a wider window


async def test_a_source_that_led_before_the_window_stays_first_when_it_updates_inside_it() -> None:
    async with session_factory() as db, db.begin():
        a, b = await feed(db), await feed(db)
        old = NOW - timedelta(days=40)
        await _cluster(
            db,
            [
                await _article(db, a, old),
                await _article(db, b, old + timedelta(minutes=30)),
                await _article(db, a, NOW - timedelta(days=1)),
            ],
            2,
        )
    timing = await _get(f"/sources/{a.id}/timing", days=7)
    assert timing["stories"] == 1 and timing["first"] == 1
    [story] = await _walk(f"/sources/{a.id}/clusters", 10)
    assert story["first"] is True  # the same answer as the story list


async def test_clusters_carry_this_sources_position_and_page_consistently() -> None:
    source_id = await _timing_fixture()
    full = await _walk(f"/sources/{source_id}/clusters", 100)
    assert len(full) == 8  # every story the source appears in, not only the window
    kinds = sorted((str(i["first"]), i["minutes_behind"]) for i in full)
    assert kinds.count(("None", None)) == 1  # the single-source story has no position
    assert [i["minutes_behind"] for i in full if i["first"] is False and i["minutes_behind"] == 0.0]
    assert sorted(i["minutes_behind"] for i in full if i["first"] is False) == [
        0.0,
        60.0,
        60.0,
        120.0,
    ]
    for size in (1, 2, 3):
        assert await _walk(f"/sources/{source_id}/clusters", size) == full


async def test_articles_are_keyset_paged_by_effective_date_and_belong_to_the_source() -> None:
    async with session_factory() as db, db.begin():
        source, other = await feed(db), await feed(db)
        same = BASE
        for offset in (0, 1, 1, 2, 3):
            await _article(db, source, same + timedelta(hours=offset))
        await _article(db, other, same)
        source_id = source.id
    full = await _walk(f"/sources/{source_id}/articles", 100)
    assert len(full) == 5
    dates = [i["first_discovered_at"] for i in full]
    assert dates == sorted(dates, reverse=True)
    assert all(p["feed_id"] == str(source_id) for i in full for p in i["provenance"])
    for size in (1, 2, 3):
        assert await _walk(f"/sources/{source_id}/articles", size) == full


async def test_fetches_page_newest_first() -> None:
    async with session_factory() as db, db.begin():
        source = await feed(db)
        for hours in range(5):
            await _fetch(db, source, NOW - timedelta(hours=hours), "success")
        source_id = source.id
    full = await _walk(f"/sources/{source_id}/fetches", 100)
    assert [i["started_at"] for i in full] == sorted((i["started_at"] for i in full), reverse=True)
    for size in (1, 2):
        assert await _walk(f"/sources/{source_id}/fetches", size) == full


async def _statements(path: str, **params: Any) -> int:
    seen: list[str] = []

    def record(conn, cursor, statement, *_):  # type: ignore[no-untyped-def]
        seen.append(statement)

    sa_event.listen(Engine, "before_cursor_execute", record)
    try:
        await _get(path, **params)
    finally:
        sa_event.remove(Engine, "before_cursor_execute", record)
    return len(seen)


async def test_a_page_costs_the_same_number_of_statements_however_many_rows_it_has() -> None:
    async with session_factory() as db, db.begin():
        small, large = await feed(db), await feed(db)
        for source, count in ((small, 3), (large, 30)):
            for i in range(count):
                mine = await _article(db, source, BASE + timedelta(minutes=i))
                await _cluster(db, [mine, await _article(db, await feed(db), BASE)], 2)
        ids = small.id, large.id
    for suffix in ("/articles", "/clusters", "/fetches", "/coverage", "/timing", ""):
        counts = (
            {await _statements(f"/sources/{i}{suffix}", limit=100) for i in ids}
            if suffix in ("/articles", "/clusters", "/fetches")
            else {await _statements(f"/sources/{i}{suffix}") for i in ids}
        )
        assert len(counts) == 1, (suffix, counts)


async def test_the_window_queries_are_served_by_indexes() -> None:
    from app.sources import queries

    async with session_factory() as db, db.begin():
        source_id = (await feed(db)).id
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        start = queries.window_start(30)
        for statement in (
            queries.scoped_articles(source_id, start),
            queries.positions(source_id, start),
        ):
            sql = str(
                statement.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True})
            )
            plan = "\n".join(row[0] for row in (await db.execute(text(f"EXPLAIN {sql}"))).all())
            assert "Seq Scan on feed_articles" not in plan, plan
            assert "Seq Scan on articles" not in plan, plan
            assert "Seq Scan on story_cluster_members" not in plan, plan
