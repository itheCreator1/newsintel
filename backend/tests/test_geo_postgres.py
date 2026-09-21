import os
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from event_fixtures import BASE, annotate, entities, feed
from test_compare_postgres import (
    NOW,
    _article,
    _cluster,
    _country,
    _free_code,
    _get,
    _statements,
    _status,
    _walk,
)

from app.db.session import session_factory
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
_WORLD: SimpleNamespace | None = None


async def _world() -> SimpleNamespace:
    """A small hand-made world, built once (its numbers are asserted below).

    a1  s1     story US  cluster c1 (a1, a3)
    a2  s1     story GR  cluster c2 (a2)
    a3  s2     story GR  cluster c1, mentioned US and GR
    a4  s2+s1  story US  cluster c3 (a4, a5)
    a5  s3     mentioned GR (current) and US (not current)   cluster c3
    a6  s3     no country at all
    old s1     story US, 40 days before now, outside a 30-day window
    s1 is stored as US, s2 as lower-case gr, s3 has no country.
    """
    global _WORLD
    if _WORLD is not None:
        return _WORLD
    async with session_factory() as db, db.begin():
        us, gr = await _free_code(db), await _free_code(db)
        assert us != gr
        s1, s2, s3 = await feed(db), await feed(db), await feed(db)
        s1.source_country, s2.source_country = us, gr.lower()
        (e1,) = await entities(db, 1)
        day = BASE.replace(hour=6)
        a1 = await _article(db, s1, day)
        a2 = await _article(db, s1, day + timedelta(hours=1))
        a3 = await _article(db, s2, day + timedelta(hours=2))
        a4 = await _article(db, s2, day + timedelta(hours=3), also=(s1,))
        a5 = await _article(db, s3, day + timedelta(hours=4))
        a6 = await _article(db, s3, day + timedelta(hours=5))
        old = await _article(db, s1, NOW - timedelta(days=40))
        await annotate(db, a1.id, [e1], country=us)
        await annotate(db, a2.id, [e1], country=gr)
        await annotate(db, a3.id, [e1], country=gr)
        await annotate(db, a4.id, [e1], country=us)
        await annotate(db, a5.id, [e1])
        await annotate(db, a6.id, [e1])
        await annotate(db, old.id, [e1], country=us)
        await _country(db, a3, us, "mentioned")
        await _country(db, a3, gr, "mentioned")
        await _country(db, a5, gr, "mentioned")
        await _country(db, a5, us, "mentioned", current=False)
        c1 = await _cluster(db, [a1, a3], 2)
        c2 = await _cluster(db, [a2], 1)
        c3 = await _cluster(db, [a4, a5], 2)
        span = (NOW - timedelta(days=2), NOW - timedelta(days=1))
        db.add_all(
            [
                Event(algorithm_version=EVENT_ALGORITHM_VERSION, primary_country=us,
                      started_at=span[0], ended_at=span[1]),
                Event(algorithm_version=EVENT_ALGORITHM_VERSION, primary_country=us,
                      started_at=span[0], ended_at=span[1], status="closed"),
                Event(algorithm_version=EVENT_ALGORITHM_VERSION, primary_country=gr,
                      started_at=span[0], ended_at=span[1]),
                Event(algorithm_version=EVENT_ALGORITHM_VERSION, primary_country=None,
                      started_at=span[0], ended_at=span[1]),
                Event(algorithm_version="older-1", primary_country=us,
                      started_at=span[0], ended_at=span[1]),
                Event(algorithm_version=EVENT_ALGORITHM_VERSION, primary_country=us,
                      started_at=NOW - timedelta(days=41), ended_at=NOW - timedelta(days=40)),
            ]
        )  # fmt: skip
        _WORLD = SimpleNamespace(
            us=us, gr=gr,
            a=SimpleNamespace(a1=a1.id, a2=a2.id, a3=a3.id, a4=a4.id, a5=a5.id, a6=a6.id),
            c1=c1.id, c2=c2.id, c3=c3.id,
        )  # fmt: skip
    return _WORLD


async def _rows(role: str, w: SimpleNamespace, **params: Any) -> dict[str, dict[str, Any]]:
    body = await _get("/geo/countries", role=role, **params)
    return {
        item["country_code"]: item for item in body["items"] if item["country_code"] in (w.us, w.gr)
    }


def _counts(item: dict[str, Any]) -> tuple[Any, Any, Any]:
    return item["articles"], item["stories"], item["sources"]


async def test_story_countries_count_their_own_articles_and_stories() -> None:
    w = await _world()
    rows = await _rows("story", w)
    assert _counts(rows[w.us]) == (2, 2, None)  # a1 a4 in c1 c3; the old one is outside the window
    assert _counts(rows[w.gr]) == (2, 2, None)  # a2 a3 in c2 c1
    assert rows[w.us]["events"] is None


async def test_mentioned_countries_use_current_annotations_and_never_the_story_role() -> None:
    w = await _world()
    rows = await _rows("mentioned", w)
    assert _counts(rows[w.us]) == (1, 1, None)  # only a3: a5's US row is not current
    assert _counts(rows[w.gr]) == (2, 2, None)  # a3 (c1) and a5 (c3)


async def test_source_countries_group_mixed_case_and_count_feeds() -> None:
    w = await _world()
    rows = await _rows("source", w)
    assert _counts(rows[w.us]) == (3, 3, 1)  # s1 carries a1 a2 a4
    assert _counts(rows[w.gr]) == (2, 2, 1)  # s2, stored lower-case, carries a3 a4
    body = await _get("/geo/countries", role="source")
    assert all(item["country_code"] == item["country_code"].upper() for item in body["items"])


async def test_roles_are_separate_views_that_are_never_added() -> None:
    w = await _world()
    per_role = {r: (await _rows(r, w))[w.gr]["articles"] for r in ("story", "mentioned", "source")}
    assert per_role == {"story": 2, "mentioned": 2, "source": 2}
    ids = {r: {i["id"] for i in await _walk("/geo/articles", 50, role=r, code=w.us)}
           for r in ("story", "mentioned", "source")}  # fmt: skip
    a = w.a
    assert ids["story"] == {str(a.a1), str(a.a4)}
    assert ids["mentioned"] == {str(a.a3)}
    assert ids["source"] == {str(a.a1), str(a.a2), str(a.a4)}


async def test_events_are_counted_per_country_for_the_current_version_and_window() -> None:
    w = await _world()
    rows = await _rows("event", w)
    assert (rows[w.us]["events"], rows[w.gr]["events"]) == (2, 1)  # not older-1, not 40 days ago
    assert rows[w.us]["articles"] is None and rows[w.us]["stories"] is None
    body = await _get("/geo/countries", role="event")
    assert body["coverage"]["unit"] == "events"
    assert 0 < body["coverage"]["located"] < body["coverage"]["window_total"]  # one has no country


async def test_coverage_names_its_base_and_counts_unlocated_articles() -> None:
    w = await _world()
    for role, at_least in (("story", 4), ("mentioned", 2), ("source", 4)):
        coverage = (await _get("/geo/countries", role=role))["coverage"]
        assert coverage["unit"] == "articles"
        assert coverage["located"] < coverage["window_total"]  # a6 has no country in any role
        assert coverage["located"] >= at_least
    assert w.a.a6


async def test_the_map_count_equals_its_evidence_at_every_page_size() -> None:
    w = await _world()
    for role in ("story", "mentioned", "source"):
        for code in (w.us, w.gr):
            expected = (await _rows(role, w))[code]["articles"]
            full = await _walk("/geo/articles", 100, role=role, code=code)
            assert len({i["id"] for i in full}) == len(full) == expected, (role, code)
            for size in (1, 2, 3):
                paged = await _walk("/geo/articles", size, role=role, code=code)
                assert [i["id"] for i in paged] == [i["id"] for i in full], (role, code, size)


async def test_a_country_agrees_with_the_comparison_of_the_same_role() -> None:
    w = await _world()
    for role in ("story", "mentioned", "source"):
        rows = await _rows(role, w)
        body = await _get("/compare", kind="country", a=w.us, b=w.gr, role=role)
        assert body["a"]["articles"] == rows[w.us]["articles"], role
        assert body["b"]["articles"] == rows[w.gr]["articles"], role
        assert body["a"]["stories"] == rows[w.us]["stories"], role


async def test_a_lower_case_code_is_accepted_and_the_window_bounds_the_evidence() -> None:
    w = await _world()
    upper = await _walk("/geo/articles", 50, role="story", code=w.gr)
    assert {i["id"] for i in upper} == {
        i["id"] for i in await _walk("/geo/articles", 50, role="story", code=w.gr.lower())
    }
    wide = await _walk("/geo/articles", 50, role="story", code=w.us, days=60)
    assert len(wide) == 3  # the 40-day-old article joins


async def test_access_and_validation() -> None:
    w = await _world()
    assert await _status("/geo/countries", signed_in=False, role="story") == 401
    assert await _status("/geo/articles", signed_in=False, role="story", code=w.us) == 401
    assert await _status("/geo/countries") == 422
    assert await _status("/geo/countries", role="everywhere") == 422
    assert await _status("/geo/countries", role="story", days=0) == 422
    assert await _status("/geo/articles", role="event", code=w.us) == 422
    assert await _status("/geo/articles", role="story", code="USA") == 422
    assert await _status("/geo/articles", role="story", code="U1") == 422
    assert await _status("/geo/articles", role="story", code=w.us, cursor="not-a-cursor") == 400


async def test_the_statement_count_does_not_depend_on_the_data() -> None:
    w = await _world()
    for role in ("story", "mentioned", "source", "event"):
        few = await _statements("/geo/countries", role=role, days=1)
        many = await _statements("/geo/countries", role=role, days=90)
        assert few == many <= 3, role
    assert w.us
