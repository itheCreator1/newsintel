import os
import random
import string
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from event_fixtures import BASE, annotate, entities, feed
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

from app.auth.models import Session
from app.auth.routes import current_session
from app.clustering.models import StoryCluster, StoryClusterMember
from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.main import create_app
from app.nlp.models import ArticleCountryAnnotation, NlpProcessorRun

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


async def _article(  # type: ignore[no-untyped-def]
    db, source: Feed, at: datetime, *, also: tuple[Feed, ...] = ()
) -> Article:
    article = Article(
        id=uuid.uuid4(),
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title=f"Article {uuid.uuid4().hex[:6]}",
        normalized_title_hash=uuid.uuid4().hex,
        published_at=at,
        first_discovered_at=at,
    )
    db.add(article)
    await db.flush()
    for carrier in (source, *also):
        db.add(
            FeedArticle(
                feed_id=carrier.id,
                article_id=article.id,
                guid=uuid.uuid4().hex,
                feed_title=article.title,
                feed_url=article.original_url,
                metadata_json={},
                discovered_at=at,
            )
        )
    await db.flush()
    return article


async def _cluster(db, members: list[Article], sources: int) -> StoryCluster:  # type: ignore[no-untyped-def]
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


async def _country(  # type: ignore[no-untyped-def]
    db, article: Article, code: str, role: str, *, current: bool = True
) -> None:
    run_id = await db.scalar(
        select(NlpProcessorRun.id).where(NlpProcessorRun.article_id == article.id)
    )
    db.add(
        ArticleCountryAnnotation(
            article_id=article.id,
            run_id=run_id,
            country_code=code,
            role=role,
            inferred=False,
            rule_version="test",
            occurrence_count=1,
            occurrences=[],
            input_fingerprint="a" * 64,
            is_current=current,
        )
    )
    await db.flush()


async def _free_code(db) -> str:  # type: ignore[no-untyped-def]
    """Two letters no other test data uses, so country counts here are exactly what this builds."""
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=2))
        if code in {"ZZ", "ZY"}:  # the empty-countries test relies on these two being unused
            continue
        used = await db.scalar(
            select(func.count())
            .select_from(ArticleCountryAnnotation)
            .where(ArticleCountryAnnotation.country_code == code)
        ) or await db.scalar(
            select(func.count()).select_from(Feed).where(func.upper(Feed.source_country) == code)
        )
        if not used:
            return code


_WORLD: SimpleNamespace | None = None


async def _world() -> SimpleNamespace:
    """A small hand-made world, built once (its numbers are asserted below).

    a1  s1     e1 e2   story US  cluster c1 (a1, a3)
    a2  s1     e1      story GR  cluster c2 (a2)
    a3  s2     e2      story GR  cluster c1, mentioned US and GR
    a4  s2+s1  e1 e2 e3 story US cluster c3 (a4, a5)
    a5  s3     e3      mentioned GR (current) and US (not current)
    old s1     e1 e2   40 days before now, outside a 30-day window
    """
    global _WORLD
    if _WORLD is not None:
        return _WORLD
    async with session_factory() as db, db.begin():
        us, gr = await _free_code(db), await _free_code(db)
        assert us != gr
        s1, s2, s3 = await feed(db), await feed(db), await feed(db)
        s1.source_country, s2.source_country = us, gr.lower()  # stored unnormalised
        e1, e2, e3 = await entities(db, 3)
        day = BASE.replace(hour=6)
        a1 = await _article(db, s1, day)
        a2 = await _article(db, s1, day + timedelta(hours=1))
        a3 = await _article(db, s2, day + timedelta(hours=2))
        a4 = await _article(db, s2, day + timedelta(hours=3), also=(s1,))
        a5 = await _article(db, s3, day + timedelta(hours=4))
        old = await _article(db, s1, NOW - timedelta(days=40))
        await annotate(db, a1.id, [e1, e2], country=us)
        await annotate(db, a2.id, [e1], country=gr)
        await annotate(db, a3.id, [e2], country=gr)
        await annotate(db, a4.id, [e1, e2, e3], country=us)
        await annotate(db, a5.id, [e3])
        await annotate(db, old.id, [e1, e2])
        await _country(db, a3, us, "mentioned")
        await _country(db, a3, gr, "mentioned")
        await _country(db, a5, gr, "mentioned")
        await _country(db, a5, us, "mentioned", current=False)
        c1 = await _cluster(db, [a1, a3], 2)
        c2 = await _cluster(db, [a2], 1)
        c3 = await _cluster(db, [a4, a5], 2)
        _WORLD = SimpleNamespace(
            us=us, gr=gr, s1=s1.id, s2=s2.id, s3=s3.id, e1=e1.id, e2=e2.id, e3=e3.id,
            a=SimpleNamespace(a1=a1.id, a2=a2.id, a3=a3.id, a4=a4.id, a5=a5.id, old=old.id),
            c1=c1.id, c2=c2.id, c3=c3.id,
        )  # fmt: skip
    return _WORLD


def _entities(w: SimpleNamespace, a: str = "e1", b: str = "e2", **extra: Any) -> dict[str, Any]:
    return {"kind": "entity", "a": str(getattr(w, a)), "b": str(getattr(w, b)), **extra}


def _sets(overlap: dict[str, Any]) -> tuple[int, int, int, int]:
    return overlap["only_a"], overlap["both"], overlap["only_b"], overlap["union"]


async def test_entities_overlap_on_articles_stories_and_sources() -> None:
    w = await _world()
    body = await _get("/compare", **_entities(w))
    overlap = body["overlap"]
    assert _sets(overlap["articles"]) == (1, 2, 1, 4)  # a2 | a1 a4 | a3
    assert overlap["articles"]["jaccard"] == pytest.approx(0.5)
    assert _sets(overlap["stories"]) == (1, 2, 0, 3)  # c2 | c1 c3 |
    assert overlap["stories"]["jaccard"] == pytest.approx(2 / 3)
    assert _sets(overlap["sources"]) == (0, 2, 0, 2)  # s1 and s2 carry both
    assert overlap["sources"]["jaccard"] == pytest.approx(1.0)
    a, b = body["a"], body["b"]
    assert (a["articles"], a["stories"], a["sources"]) == (3, 3, 2)
    assert (b["articles"], b["stories"], b["sources"]) == (3, 2, 2)
    assert a["subject"]["ref"] == str(w.e1) and a["subject"]["label"].startswith("Entity")
    assert body["window_days"] == 30 and body["role"] is None


async def test_swapping_the_subjects_swaps_the_sides_and_keeps_the_symmetric_numbers() -> None:
    w = await _world()
    for params in (
        _entities(w),
        {"kind": "source", "a": str(w.s1), "b": str(w.s2)},
        {"kind": "country", "a": w.us, "b": w.gr, "role": "story"},
    ):
        forward = await _get("/compare", **params)
        backward = await _get("/compare", **{**params, "a": params["b"], "b": params["a"]})
        for name in ("articles", "stories") + (() if params["kind"] == "source" else ("sources",)):
            f, r = forward["overlap"][name], backward["overlap"][name]
            assert (f["only_a"], f["only_b"]) == (r["only_b"], r["only_a"]), (params, name)
            assert (f["both"], f["union"], f["jaccard"]) == (r["both"], r["union"], r["jaccard"])
        assert forward["a"] == backward["b"] and forward["b"] == backward["a"]
        assert {i["id"] for i in forward["related"]["entities"]} == {
            i["id"] for i in backward["related"]["entities"]
        }
        for row in forward["related"]["entities"]:
            mirrored = next(i for i in backward["related"]["entities"] if i["id"] == row["id"])
            assert (row["a_articles"], row["b_articles"]) == (
                mirrored["b_articles"],
                mirrored["a_articles"],
            )


async def test_sources_compare_carried_articles_and_a_shared_article_counts_for_both() -> None:
    w = await _world()
    body = await _get("/compare", kind="source", a=str(w.s1), b=str(w.s2))
    assert _sets(body["overlap"]["articles"]) == (2, 1, 1, 4)  # a1 a2 | a4 | a3
    assert body["overlap"]["articles"]["jaccard"] == pytest.approx(0.25)
    assert _sets(body["overlap"]["stories"]) == (1, 2, 0, 3)
    assert body["overlap"]["sources"] is None and body["related"]["sources"] is None
    assert body["a"]["sources"] is None and body["b"]["sources"] is None
    assert body["a"]["subject"]["ref"] == str(w.s1) and body["b"]["subject"]["kind"] == "source"


async def test_countries_compare_by_one_role_and_never_mix_roles() -> None:
    w = await _world()
    story = await _get("/compare", kind="country", a=w.us, b=w.gr, role="story")
    assert _sets(story["overlap"]["articles"]) == (2, 0, 2, 4)  # a1 a4 | | a2 a3
    assert story["overlap"]["articles"]["jaccard"] == 0.0
    assert _sets(story["overlap"]["stories"]) == (1, 1, 1, 3)  # c3 | c1 | c2
    mentioned = await _get("/compare", kind="country", a=w.us, b=w.gr, role="mentioned")
    assert _sets(mentioned["overlap"]["articles"]) == (0, 1, 1, 2)  # a3 in both, a5 only GR
    source = await _get("/compare", kind="country", a=w.us, b=w.gr.lower(), role="source")
    assert _sets(source["overlap"]["articles"]) == (2, 1, 1, 4)  # a1 a2 | a4 | a3
    assert source["b"]["subject"]["ref"] == w.gr  # normalised to upper case
    assert story["role"] == "story" and story["a"]["subject"]["role"] == "story"


async def test_related_items_count_each_side_and_leave_out_the_subjects() -> None:
    w = await _world()
    body = await _get("/compare", **_entities(w))
    entities_seen = {
        r["id"]: (r["a_articles"], r["b_articles"]) for r in body["related"]["entities"]
    }
    assert entities_seen == {str(w.e3): (1, 1)}  # only a4 has e3 among the union
    countries = {
        (r["country_code"], r["role"]): (r["a_articles"], r["b_articles"])
        for r in body["related"]["countries"]
    }
    assert countries[(w.us, "primary")] == (2, 2)  # a1 and a4 are in both sets
    assert countries[(w.gr, "primary")] == (1, 1)  # a2 only in A, a3 only in B
    assert (w.us, "mentioned") in countries
    assert {r["id"]: (r["a_articles"], r["b_articles"]) for r in body["related"]["sources"]} == {
        str(w.s1): (3, 2),  # a1 a2 a4 vs a1 a4
        str(w.s2): (1, 2),  # a4 vs a3 a4
    }
    ranked = [r["a_articles"] + r["b_articles"] for r in body["related"]["sources"]]
    assert ranked == sorted(ranked, reverse=True)
    # A country subject does not list itself under the same role.
    story = await _get("/compare", kind="country", a=w.us, b=w.gr, role="story")
    listed = {(r["country_code"], r["role"]) for r in story["related"]["countries"]}
    assert (w.us, "primary") not in listed and (w.gr, "primary") not in listed


async def test_only_current_annotations_count() -> None:
    w = await _world()
    mentioned = await _get("/compare", kind="country", a=w.us, b=w.gr, role="mentioned")
    # a5's US mention is not current, so US has just a3.
    assert mentioned["a"]["articles"] == 1 and mentioned["b"]["articles"] == 2


async def test_the_window_bounds_both_subjects_and_the_timeline_is_zero_filled() -> None:
    w = await _world()
    narrow = await _get("/compare", **_entities(w))
    wide = await _get("/compare", **_entities(w, days=60))
    assert _sets(narrow["overlap"]["articles"]) == (1, 2, 1, 4)
    assert _sets(wide["overlap"]["articles"]) == (1, 3, 1, 5)  # the 40-day-old article joins both
    assert wide["window_days"] == 60 and len(wide["a"]["timeline"]) == 60
    for side in ("a", "b"):
        days = [d["date"] for d in narrow[side]["timeline"]]
        assert len(days) == 30 and days == sorted(set(days))
        assert sum(d["article_count"] for d in narrow[side]["timeline"]) == narrow[side]["articles"]
    assert [d["date"] for d in narrow["a"]["timeline"]] == [
        d["date"] for d in narrow["b"]["timeline"]
    ]


async def test_empty_subjects_give_zero_counts_and_a_null_jaccard() -> None:
    async with session_factory() as db, db.begin():
        first, second = await entities(db, 2)
        quiet = await feed(db), await feed(db)
        ids = str(first.id), str(second.id)
    body = await _get("/compare", kind="entity", a=ids[0], b=ids[1])
    for name in ("articles", "stories", "sources"):
        assert _sets(body["overlap"][name]) == (0, 0, 0, 0)
        assert body["overlap"][name]["jaccard"] is None
    assert body["related"] == {"entities": [], "countries": [], "sources": []}
    assert all(d["article_count"] == 0 for d in body["a"]["timeline"])
    other = await _get("/compare", kind="source", a=str(quiet[0].id), b=str(quiet[1].id))
    assert other["overlap"]["articles"]["jaccard"] is None and other["overlap"]["sources"] is None
    nowhere = await _get("/compare", kind="country", a="ZZ", b="ZY", role="story")
    assert nowhere["overlap"]["articles"]["jaccard"] is None


async def test_a_retired_source_can_still_be_compared() -> None:
    w = await _world()
    async with session_factory() as db, db.begin():
        retired = await feed(db)
        retired.retired_at = NOW
        retired_id = retired.id
    body = await _get("/compare", kind="source", a=str(retired_id), b=str(w.s1))
    assert body["a"]["subject"]["retired"] is True and body["b"]["subject"]["retired"] is False


async def test_evidence_parts_partition_the_union_and_page_consistently() -> None:
    w = await _world()
    params = _entities(w)
    seen: dict[str, set[str]] = {}
    for part in ("a", "both", "b"):
        full = await _walk("/compare/articles", 100, part=part, **params)
        seen[part] = {item["id"] for item in full}
        assert len(seen[part]) == len(full)
        dates = [item["published_at"] for item in full]
        assert dates == sorted(dates, reverse=True)
        for size in (1, 2, 3):
            assert await _walk("/compare/articles", size, part=part, **params) == full
    a = w.a
    expected = {"a": {a.a2}, "both": {a.a1, a.a4}, "b": {a.a3}}
    assert seen == {part: {str(i) for i in ids} for part, ids in expected.items()}
    assert not (seen["a"] & seen["both"] or seen["b"] & seen["both"] or seen["a"] & seen["b"])
    body = await _get("/compare", **params)
    assert sum(len(v) for v in seen.values()) == body["overlap"]["articles"]["union"]


async def test_story_parts_partition_the_union_and_carry_each_sides_article_counts() -> None:
    w = await _world()
    params = _entities(w)
    found: dict[str, list[dict[str, Any]]] = {}
    for part in ("a", "both", "b"):
        full = await _walk("/compare/stories", 100, part=part, **params)
        found[part] = full
        for size in (1, 2):
            assert await _walk("/compare/stories", size, part=part, **params) == full
    assert [s["id"] for s in found["a"]] == [str(w.c2)]
    assert {s["id"] for s in found["both"]} == {str(w.c1), str(w.c3)}
    assert found["b"] == []
    counts = {s["id"]: (s["a_articles"], s["b_articles"]) for s in found["both"]}
    assert counts == {str(w.c1): (1, 2), str(w.c3): (1, 1)}
    assert found["a"][0]["a_articles"] == 1 and found["a"][0]["b_articles"] == 0


async def test_unknown_malformed_and_unauthorised_requests_are_refused() -> None:
    w = await _world()
    good = _entities(w)
    missing = str(uuid.uuid4())
    assert await _status("/compare", **{**good, "b": missing}) == 404
    assert await _status("/compare", kind="source", a=str(w.s1), b=missing) == 404
    assert await _status("/compare", **{**good, "b": "not-a-uuid"}) == 422
    assert await _status("/compare", **{**good, "b": good["a"]}) == 422  # a subject with itself
    assert await _status("/compare", kind="country", a=w.us, b=w.gr) == 422  # role required
    assert await _status("/compare", kind="country", a=w.us, b=w.us.lower(), role="story") == 422
    assert await _status("/compare", kind="country", a=w.us, b="USA", role="story") == 422
    assert await _status("/compare", **{**good, "role": "story"}) == 422  # role only for countries
    assert await _status("/compare", **{**good, "role": "nope"}) == 422
    assert await _status("/compare", **{**good, "kind": "keyword"}) == 422
    assert await _status("/compare", **{**good, "days": 0}) == 422
    assert await _status("/compare", **{**good, "days": 367}) == 422
    assert await _status("/compare/articles", **good) == 422  # part is required
    assert await _status("/compare/articles", part="all", **good) == 422
    assert await _status("/compare/stories", part="a", cursor="not-a-cursor", **good) == 400
    assert await _status("/compare/articles", part="a", limit=0, **good) == 422
    assert await _status("/compare/articles", part="a", limit=101, **good) == 422
    for path in ("/compare", "/compare/articles", "/compare/stories"):
        assert await _status(path, signed_in=False, part="a", **good) == 401


async def test_a_request_costs_the_same_number_of_statements_however_many_rows_it_has() -> None:
    async with session_factory() as db, db.begin():
        small, large = await entities(db, 2), await entities(db, 2)
        origin = await feed(db)
        for pair, count in ((small, 3), (large, 30)):
            for i in range(count):
                mine = await _article(db, origin, BASE + timedelta(minutes=i))
                await annotate(db, mine.id, list(pair), country="GB")
                await _cluster(db, [mine, await _article(db, await feed(db), BASE)], 2)
        pairs = [(str(p[0].id), str(p[1].id)) for p in (small, large)]
    for path, extra in (("/compare", {}), ("/compare/articles", {"part": "both"}),
                        ("/compare/stories", {"part": "both"})):  # fmt: skip
        counts = {
            await _statements(path, kind="entity", a=a, b=b, limit=100, **extra)
            if path != "/compare"
            else await _statements(path, kind="entity", a=a, b=b, **extra)
            for a, b in pairs
        }
        assert len(counts) == 1, (path, counts)


async def test_the_subject_queries_have_an_index_path() -> None:
    # With sequential scans disabled, so on near-empty tables this proves a path exists, not that
    # the planner prefers it at scale (the 13B measurement on 60k articles is in the roadmap).
    from app.compare import queries

    async with session_factory() as db, db.begin():
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        start = queries.window_start(30)
        subjects = (
            ("entity", str(uuid.uuid4()), None),
            ("source", str(uuid.uuid4()), None),
            ("country", "US", "story"),
            ("country", "US", "mentioned"),
            ("country", "US", "source"),
        )
        for kind, ref, role in subjects:
            statement = queries.subject_articles(kind, ref, role, start)
            sql = str(
                statement.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True})
            )
            plan = "\n".join(row[0] for row in (await db.execute(text(f"EXPLAIN {sql}"))).all())
            for table in ("article_entities", "article_country_annotations", "feed_articles"):
                assert f"Seq Scan on {table}" not in plan, (kind, role, plan)
            assert "Seq Scan on articles" not in plan, (kind, role, plan)
