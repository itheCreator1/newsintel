"""One list of investigations: saved searches and monitors, a saved search watched in place."""

import asyncio
import os
from datetime import timedelta
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, update
from test_monitor_api_postgres import CSRF, T1, _client, _make, _user

from app.db.session import session_factory
from app.monitors.models import Monitor

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _save(client: httpx.AsyncClient, name: str, q: str = "grid") -> dict[str, Any]:
    response = await client.post(
        "/api/v1/saved-searches", json={"name": name, "state": {"q": q}}, headers=CSRF
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _list(client: httpx.AsyncClient, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        response = await client.get(
            "/api/v1/investigations", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            return items


def _rows(items: list[dict[str, Any]]) -> list[tuple[str, bool, bool | None]]:
    """Name, whether a saved search backs the row, and whether it is watched (None: never)."""
    return [
        (
            item["name"],
            item["saved_search"] is not None,
            item["monitor"]["enabled"] if item["monitor"] else None,
        )
        for item in items
    ]


async def test_watching_a_saved_search_keeps_one_row_and_unwatching_only_pauses_it() -> None:
    async with _client(await _user()) as client:
        saved = await _save(client, "Energy grid", "grid")
        await _save(client, "Elections")
        await _make(client, "Acme watch", "acme")

        assert _rows(await _list(client, order="name")) == [
            ("Acme watch", False, True),
            ("Elections", True, None),
            ("Energy grid", True, None),
        ]

        watched = await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)
        assert watched.status_code == 200, watched.text
        monitor = watched.json()
        assert (monitor["name"], monitor["kind"], monitor["state"]["q"]) == (
            "Energy grid",
            "search",
            "grid",
        )
        # Watching again is the same monitor, not a second one.
        again = await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)
        assert again.json()["id"] == monitor["id"]

        rows = await _list(client, order="name")
        assert _rows(rows) == [
            ("Acme watch", False, True),
            ("Elections", True, None),
            ("Energy grid", True, True),
        ]
        assert rows[2]["id"] == saved["id"] and rows[2]["monitor"]["id"] == monitor["id"]

        # Unwatching pauses the monitor, so its history is kept for the next time.
        paused = await client.patch(
            f"/api/v1/monitors/{monitor['id']}", json={"enabled": False}, headers=CSRF
        )
        assert paused.status_code == 200
        assert _rows(await _list(client, order="name"))[2] == ("Energy grid", True, False)
        resumed = await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)
        assert (resumed.json()["id"], resumed.json()["enabled"]) == (monitor["id"], True)


async def test_renaming_or_deleting_a_watched_saved_search_carries_its_monitor() -> None:
    async with _client(await _user()) as client:
        saved = await _save(client, "Energy grid")
        monitor = (
            await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)
        ).json()

        renamed = await client.patch(
            f"/api/v1/saved-searches/{saved['id']}", json={"name": "Power grid"}, headers=CSRF
        )
        assert renamed.status_code == 200
        assert (await client.get(f"/api/v1/monitors/{monitor['id']}")).json()["name"] == (
            "Power grid"
        )
        # A rename from the monitor's page renames the saved search too.
        await client.patch(
            f"/api/v1/monitors/{monitor['id']}", json={"name": "Grid watch"}, headers=CSRF
        )
        assert (await client.get(f"/api/v1/saved-searches/{saved['id']}")).json()["name"] == (
            "Grid watch"
        )

        # Deleting only the monitor leaves the saved search, unwatched.
        await client.delete(f"/api/v1/monitors/{monitor['id']}", headers=CSRF)
        assert _rows(await _list(client)) == [("Grid watch", True, None)]

        monitor = (
            await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)
        ).json()
        deleted = await client.delete(f"/api/v1/saved-searches/{saved['id']}", headers=CSRF)
        assert deleted.status_code == 204
        assert (await client.get(f"/api/v1/monitors/{monitor['id']}")).status_code == 404
        assert await _list(client) == []


async def test_a_name_taken_by_another_monitor_is_refused() -> None:
    async with _client(await _user()) as client:
        saved = await _save(client, "Energy grid")
        await _make(client, "energy GRID")

        watched = await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)

        assert watched.status_code == 409
        assert "energy GRID" in watched.json()["detail"]


async def test_activity_order_puts_the_latest_match_first_and_pages_through_both_kinds() -> None:
    async with _client(await _user()) as client:
        quiet = await _save(client, "Quiet")
        busy = await _save(client, "Busy")
        busy_monitor = (
            await client.post(f"/api/v1/saved-searches/{busy['id']}/watch", headers=CSRF)
        ).json()
        older = await _make(client, "Older match")
        await _make(client, "Never matched")
        async with session_factory() as db, db.begin():
            for monitor_id, at in ((busy_monitor["id"], T1), (older["id"], T1 - timedelta(days=1))):
                await db.execute(
                    update(Monitor).where(Monitor.id == monitor_id).values(latest_match_at=at)
                )

        names = [item["name"] for item in await _list(client, order="activity", limit=1)]

        assert names[:2] == ["Busy", "Older match"]
        assert sorted(names[2:]) == ["Never matched", "Quiet"]
        assert quiet["id"] in {item["id"] for item in await _list(client, order="activity")}


async def test_the_downgrade_keeps_the_links_unless_told(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config("alembic.ini")
    async with _client(await _user()) as client:
        saved = await _save(client, "Linked")
        await client.post(f"/api/v1/saved-searches/{saved['id']}/watch", headers=CSRF)
    monkeypatch.delenv("NEWSINTEL_MIGRATION_DISCARD_MONITOR_LINKS", raising=False)
    with pytest.raises(RuntimeError, match="saved_search_id"):
        await asyncio.to_thread(command.downgrade, config, "0027")
    monkeypatch.setenv("NEWSINTEL_MIGRATION_DISCARD_MONITOR_LINKS", "1")
    await asyncio.to_thread(command.downgrade, config, "0027")
    column = text(
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_name = 'monitors' AND column_name = 'saved_search_id'"
    )
    try:
        async with session_factory() as db:
            assert await db.scalar(column) == 0
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with session_factory() as db:
        assert await db.scalar(column) == 1
