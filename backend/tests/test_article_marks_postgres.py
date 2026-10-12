"""Read, starred and a note per article: the reader's own marks, kept in Postgres."""

import asyncio
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from event_fixtures import entities, story
from sqlalchemy import select, text
from test_compare_postgres import _get, build_world
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article
from test_event_api_postgres import _make, _version

from app.clustering.models import StoryClusterMember
from app.db.session import session_factory
from app.marks.schemas import ArticleMarkUpdate
from app.marks.service import update_mark

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _new_article() -> uuid.UUID:
    async with session_factory() as db, db.begin():
        return await _article(db, [])


async def test_an_unmarked_article_is_unread_unstarred_and_has_no_note() -> None:
    article_id = await _new_article()
    async with _client() as client:
        response = await client.get(f"/articles/{article_id}/mark")
        detail = await client.get(f"/articles/{article_id}")
    assert response.status_code == 200
    assert response.json() == {
        "article_id": str(article_id),
        "read": False,
        "read_at": None,
        "starred": False,
        "note": None,
    }
    assert detail.json()["mark"] == response.json()


async def test_marks_change_one_at_a_time_and_an_empty_note_clears_it() -> None:
    article_id = await _new_article()
    async with _client() as client:
        read = await client.put(f"/articles/{article_id}/mark", json={"read": True}, headers=CSRF)
        starred = await client.put(
            f"/articles/{article_id}/mark", json={"starred": True}, headers=CSRF
        )
        noted = await client.put(
            f"/articles/{article_id}/mark",
            json={"note": "  Check the port figures.  "},
            headers=CSRF,
        )
        assert noted.status_code == 200
        body = noted.json()
        assert (body["read"], body["starred"], body["note"]) == (
            True,
            True,
            "Check the port figures.",
        )
        assert body["read_at"] is not None and body["read_at"] == read.json()["read_at"]
        assert starred.json()["note"] is None
        # The article itself says the same as its mark.
        detail = (await client.get(f"/articles/{article_id}")).json()
        assert (detail["read"], detail["starred"]) == (True, True)

        cleared = await client.put(
            f"/articles/{article_id}/mark", json={"note": "", "read": False}, headers=CSRF
        )
        assert cleared.json() | {"article_id": None} == {
            "article_id": None,
            "read": False,
            "read_at": None,
            "starred": True,
            "note": None,
        }
        too_long = await client.put(
            f"/articles/{article_id}/mark", json={"note": "x" * 4001}, headers=CSRF
        )
        assert too_long.status_code == 422
        refused = await client.put(f"/articles/{article_id}/mark", json={"starred": False})
        assert refused.status_code == 403


async def test_an_unknown_article_has_no_marks() -> None:
    async with _client() as client:
        missing = uuid.uuid4()
        assert (await client.get(f"/articles/{missing}/mark")).status_code == 404
        put = await client.put(f"/articles/{missing}/mark", json={"read": True}, headers=CSRF)
        assert put.status_code == 404


async def test_the_article_list_says_which_are_read_and_starred() -> None:
    read_id, starred_id, plain_id = await _new_article(), await _new_article(), await _new_article()
    async with _client() as client:
        await client.put(f"/articles/{read_id}/mark", json={"read": True}, headers=CSRF)
        await client.put(f"/articles/{starred_id}/mark", json={"starred": True}, headers=CSRF)
        items = (await client.get("/articles", params={"limit": 100})).json()["items"]
    marks = {item["id"]: (item["read"], item["starred"]) for item in items}
    assert marks[str(read_id)] == (True, False)
    assert marks[str(starred_id)] == (False, True)
    assert marks[str(plain_id)] == (False, False)


def _marks(items: list[dict[str, object]]) -> dict[str, tuple[object, object]]:
    return {str(item["id"]): (item["read"], item["starred"]) for item in items}


async def test_every_article_list_says_which_are_read_and_starred() -> None:
    w = await build_world()
    async with session_factory() as db, db.begin():
        await update_mark(db, w.a.a1, ArticleMarkUpdate(starred=True))
        await update_mark(db, w.a.a2, ArticleMarkUpdate(read=True))
        cluster = await story(db, await entities(db, 1), articles=2)
        event = await _make(db, _version(), [cluster])
        members = list(
            await db.scalars(
                select(StoryClusterMember.article_id).where(
                    StoryClusterMember.cluster_id == cluster.id
                )
            )
        )
        await update_mark(db, members[0], ArticleMarkUpdate(read=True, starred=True))
    a1, a2, a4 = str(w.a.a1), str(w.a.a2), str(w.a.a4)

    for path in (f"/entities/{w.e1}/articles", f"/sources/{w.s1}/articles"):
        marks = _marks((await _get(path, limit=100))["items"])
        assert (marks[a1], marks[a2], marks[a4]) == ((False, True), (True, False), (False, False))
    compared = {"kind": "entity", "a": str(w.e1), "b": str(w.e2)}
    assert _marks((await _get("/compare/articles", **compared, part="a"))["items"]) == {
        a2: (True, False)
    }
    assert _marks((await _get("/compare/articles", **compared, part="both"))["items"]) == {
        a1: (False, True),
        a4: (False, False),
    }
    mapped = _marks((await _get("/geo/articles", role="story", code=w.us, limit=100))["items"])
    assert (mapped[a1], mapped[a4]) == ((False, True), (False, False))
    events = _marks((await _get(f"/events/{event.id}/articles"))["items"])
    assert events[str(members[0])] == (True, True)
    assert events[str(members[1])] == (False, False)


async def test_the_downgrade_keeps_marks_unless_told(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config("alembic.ini")
    article_id = await _new_article()
    async with _client() as client:
        await client.put(f"/articles/{article_id}/mark", json={"starred": True}, headers=CSRF)
    monkeypatch.delenv("NEWSINTEL_MIGRATION_DISCARD_ARTICLE_MARKS", raising=False)
    with pytest.raises(RuntimeError, match="article_marks"):
        await asyncio.to_thread(command.downgrade, config, "0026")
    monkeypatch.setenv("NEWSINTEL_MIGRATION_DISCARD_ARTICLE_MARKS", "1")
    await asyncio.to_thread(command.downgrade, config, "0026")
    try:
        async with session_factory() as db:
            tables = set(await db.scalars(text("SELECT tablename FROM pg_tables")))
        assert "article_marks" not in tables
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with session_factory() as db:
        tables = set(await db.scalars(text("SELECT tablename FROM pg_tables")))
    assert "article_marks" in tables
