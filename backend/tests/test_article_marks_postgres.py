"""Read, starred and a note per article: the reader's own marks, kept in Postgres."""

import asyncio
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article

from app.db.session import session_factory

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
