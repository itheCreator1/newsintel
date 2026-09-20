import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from app.auth.models import Session, User
from app.auth.routes import current_session
from app.db.session import session_factory
from app.investigations.schemas import InvestigationState
from app.main import create_app
from app.monitors.evaluation import Snapshot, StaleClaim, Window, _publish, claim_monitor
from app.monitors.models import Monitor

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

CSRF = {"X-CSRF-Token": "csrf-token"}
T1 = datetime(2026, 9, 20, 10, tzinfo=UTC)
T2 = T1 + timedelta(hours=1)
T3 = T1 + timedelta(hours=2)


async def _user() -> uuid.UUID:
    async with session_factory() as db:
        user = User(username=f"analyst-{uuid.uuid4().hex}", password_hash="unused")
        db.add(user)
        await db.commit()
        return user.id


@asynccontextmanager
async def _client(user_id: uuid.UUID) -> AsyncIterator[httpx.AsyncClient]:
    login = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        csrf_token=CSRF["X-CSRF-Token"],
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    app = create_app()
    app.dependency_overrides[current_session] = lambda: login
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _make(client: httpx.AsyncClient, name: str, q: str = "grid") -> dict[str, Any]:
    response = await client.post(
        "/api/v1/monitors",
        json={"name": name, "kind": "search", "state": {"q": q}},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _set(monitor_id: str, **values: Any) -> None:
    async with session_factory() as db:
        await db.execute(
            update(Monitor).where(Monitor.id == uuid.UUID(monitor_id)).values(**values)
        )
        await db.commit()


async def _row(monitor_id: str) -> Monitor:
    async with session_factory() as db:
        item = await db.get(Monitor, uuid.UUID(monitor_id))
        assert item is not None
        return item


async def _evaluated(client: httpx.AsyncClient, name: str, **values: Any) -> str:
    monitor_id = (await _make(client, name))["id"]
    await _set(monitor_id, eval_cursor_at=T2, viewed_cursor_at=T1, **values)
    return str(monitor_id)


async def test_a_monitor_is_invisible_to_other_users() -> None:
    owner, other = await _user(), await _user()
    async with _client(owner) as mine, _client(other) as theirs:
        monitor_id = (await _make(mine, "Private"))["id"]
        url = f"/api/v1/monitors/{monitor_id}"

        assert (await theirs.get(url)).status_code == 404
        assert (await theirs.patch(url, json={"name": "Taken"}, headers=CSRF)).status_code == 404
        assert (await theirs.get(f"{url}/results")).status_code == 404
        viewed = await theirs.post(f"{url}/viewed", json={"through": T1.isoformat()}, headers=CSRF)
        assert viewed.status_code == 404
        assert (await theirs.delete(url, headers=CSRF)).status_code == 404
        assert (await theirs.get("/api/v1/monitors")).json()["items"] == []
        assert (await mine.get(url)).status_code == 200


async def test_create_update_and_delete_validate_and_map_errors() -> None:
    async with _client(await _user()) as client:
        created = await _make(client, "Grid")
        assert created["enabled"] is True and created["evaluated_through"] is None
        url = f"/api/v1/monitors/{created['id']}"
        bad_kind = {"name": "X", "kind": "entity", "state": {"q": "grid"}}
        assert (
            await client.post("/api/v1/monitors", json=bad_kind, headers=CSRF)
        ).status_code == 422
        assert (await client.post("/api/v1/monitors", json={}, headers=CSRF)).status_code == 422
        no_csrf = await client.post("/api/v1/monitors", json={"name": "Y"})
        assert no_csrf.status_code == 403
        duplicate = await client.post(
            "/api/v1/monitors",
            json={"name": "grid", "kind": "search", "state": {"q": "x"}},
            headers=CSRF,
        )
        assert duplicate.status_code == 409

        other = await _make(client, "Gas", q="gas")
        assert (await client.patch(f"{url}", json={"name": "GAS"}, headers=CSRF)).status_code == 409
        assert (await client.patch(url, json={"kind": "search"}, headers=CSRF)).status_code == 422
        renamed = await client.patch(url, json={"name": "Power", "enabled": False}, headers=CSRF)
        assert renamed.json()["name"] == "Power" and renamed.json()["enabled"] is False

        assert (await client.delete(url, headers=CSRF)).status_code == 204
        assert (await client.get(url)).status_code == 404
        assert (await client.get(f"/api/v1/monitors/{other['id']}")).status_code == 200


async def test_lists_page_by_name_and_by_activity_with_unmatched_monitors_last() -> None:
    async with _client(await _user()) as client:
        ids = {name: (await _make(client, name, q=name))["id"] for name in ("d", "a", "c", "b")}
        await _set(ids["c"], latest_match_at=T2)
        await _set(ids["b"], latest_match_at=T1)

        async def walk(order: str) -> list[str]:
            names, cursor = [], None
            while True:
                params: dict[str, Any] = {"limit": 1, "order": order}
                if cursor:
                    params["cursor"] = cursor
                page = (await client.get("/api/v1/monitors", params=params)).json()
                names += [item["name"] for item in page["items"]]
                cursor = page["next_cursor"]
                if not cursor:
                    return names

        assert await walk("name") == ["a", "b", "c", "d"]
        by_activity = await walk("activity")
        unmatched = sorted(["a", "d"], key=lambda name: ids[name])
        assert by_activity == ["c", "b", *unmatched]
        bad = await client.get("/api/v1/monitors", params={"cursor": "nope", "order": "activity"})
        assert bad.status_code == 422


async def test_enabling_again_schedules_evaluation_and_a_new_target_resets_history() -> None:
    async with _client(await _user()) as client:
        monitor_id = await _evaluated(
            client, "Grid", unseen_article_count=3, unseen_cluster_count=1
        )
        url = f"/api/v1/monitors/{monitor_id}"

        off = await client.patch(url, json={"enabled": False}, headers=CSRF)
        assert off.json()["unseen_article_count"] == 3
        on = await client.patch(url, json={"enabled": True}, headers=CSRF)
        assert datetime.fromisoformat(on.json()["next_evaluation_at"]) > datetime.now(UTC) - (
            timedelta(minutes=1)
        )
        moved = await client.patch(
            url, json={"kind": "search", "state": {"q": "gas"}}, headers=CSRF
        )
        body = moved.json()
        assert body["evaluated_through"] is None and body["viewed_through"] is None
        assert (body["unseen_article_count"], body["unseen_cluster_count"]) == (0, 0)


async def _view(client: httpx.AsyncClient, monitor_id: str, through: datetime) -> httpx.Response:
    return await client.post(
        f"/api/v1/monitors/{monitor_id}/viewed", json={"through": through.isoformat()}, headers=CSRF
    )


async def test_viewing_up_to_the_evaluation_boundary_zeroes_counters_and_drops_the_lease() -> None:
    async with _client(await _user()) as client:
        monitor_id = await _evaluated(
            client,
            "Grid",
            unseen_article_count=4,
            unseen_cluster_count=2,
            claim_token="held",
            claim_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )

        response = await _view(client, monitor_id, T2)

        body = response.json()
        assert response.status_code == 200
        assert (
            body["viewed_through"]
            == body["evaluated_through"]
            == T2.isoformat().replace("+00:00", "Z")
        )
        assert (body["unseen_article_count"], body["unseen_cluster_count"]) == (0, 0)
        row = await _row(monitor_id)
        assert row.claim_token is None and row.claim_expires_at is None


async def test_viewing_an_earlier_boundary_rewinds_evaluation_and_asks_for_a_recount() -> None:
    async with _client(await _user()) as client:
        monitor_id = await _evaluated(
            client, "Grid", unseen_article_count=4, unseen_cluster_count=2
        )
        await _set(monitor_id, eval_cursor_at=T3, next_evaluation_at=T3)
        middle = T2

        response = await _view(client, monitor_id, middle)

        assert response.status_code == 200
        row = await _row(monitor_id)
        assert row.viewed_cursor_at == row.eval_cursor_at == middle
        assert (row.unseen_article_count, row.unseen_cluster_count) == (0, 0)
        assert row.next_evaluation_at > datetime.now(UTC) - timedelta(minutes=1)


async def test_view_boundaries_are_checked_and_repeat_views_change_nothing() -> None:
    async with _client(await _user()) as client:
        never = (await _make(client, "Never"))["id"]
        assert (await _view(client, never, T1)).status_code == 409
        monitor_id = await _evaluated(
            client, "Grid", unseen_article_count=4, unseen_cluster_count=2
        )

        assert (await _view(client, monitor_id, T3)).status_code == 422
        naive = await client.post(
            f"/api/v1/monitors/{monitor_id}/viewed", json={"through": "2026-09-20T10:00:00"},
            headers=CSRF,
        )  # fmt: skip
        assert naive.status_code == 422
        assert (await _view(client, monitor_id, T1)).status_code == 200  # nothing new to mark
        assert (await _row(monitor_id)).unseen_article_count == 4
        await _view(client, monitor_id, T2)
        before = await _row(monitor_id)
        again = await _view(client, monitor_id, T2)
        assert again.status_code == 200
        assert (await _row(monitor_id)).updated_at == before.updated_at


async def _leased_snapshot(client: httpx.AsyncClient) -> tuple[str, str, Snapshot]:
    monitor_id = await _evaluated(client, "Grid", unseen_article_count=4, unseen_cluster_count=2)
    await _set(monitor_id, next_evaluation_at=datetime.now(UTC) - timedelta(seconds=1))
    async with session_factory() as db:
        token = await claim_monitor(db, uuid.UUID(monitor_id), 60)
    assert token is not None
    return monitor_id, token, Snapshot("search", InvestigationState(q="grid"), T2, T1)


async def test_an_evaluation_that_started_before_a_view_cannot_publish_over_it() -> None:
    async with _client(await _user()) as client:
        monitor_id, token, snapshot = await _leased_snapshot(client)

        await _view(client, monitor_id, T2)
        with pytest.raises(StaleClaim):
            await _publish(
                uuid.UUID(monitor_id), token, snapshot, Window(T3, None, 9, 9), datetime.now(UTC)
            )

        row = await _row(monitor_id)
        assert row.viewed_cursor_at == row.eval_cursor_at == T2 and row.unseen_article_count == 0


async def test_a_view_and_a_publish_racing_never_leave_stale_unseen_counts() -> None:
    async with _client(await _user()) as client:
        monitor_id, token, snapshot = await _leased_snapshot(client)

        outcomes = await asyncio.gather(
            _view(client, monitor_id, T2),
            _publish(
                uuid.UUID(monitor_id), token, snapshot, Window(T3, None, 9, 9), datetime.now(UTC)
            ),
            return_exceptions=True,
        )

        assert not isinstance(outcomes[0], BaseException) and outcomes[0].status_code == 200
        assert outcomes[1] is None or isinstance(outcomes[1], StaleClaim)
        row = await _row(monitor_id)
        assert row.viewed_cursor_at == row.eval_cursor_at == T2
        assert (row.unseen_article_count, row.unseen_cluster_count) == (0, 0)


async def test_results_are_empty_before_evaluation_and_report_an_unusable_state() -> None:
    async with _client(await _user()) as client:
        monitor_id = (await _make(client, "Grid"))["id"]
        url = f"/api/v1/monitors/{monitor_id}/results"

        empty = (await client.get(url)).json()
        assert empty["items"] == [] and empty["next_cursor"] is None and empty["window_end"] is None
        forged = await client.get(url, params={"cursor": "AAAA"})
        assert forged.status_code == 422

        await _set(monitor_id, eval_cursor_at=T2, viewed_cursor_at=T1, state={"retired": True})
        broken = await client.get(url)
        assert (
            broken.status_code == 409 and broken.json()["detail"]["code"] == "invalid_monitor_state"
        )
