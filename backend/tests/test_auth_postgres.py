"""The account from the UI: a new password, signing out other browsers, the sign-in limit."""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.auth.limits import LOCKOUT, MAX_FAILURES, WINDOW, LoginFailure
from app.auth.models import User
from app.auth.security import hash_password
from app.db.session import session_factory
from app.main import create_app

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

FIRST = "first-password-123"
SECOND = "second-password-456"


async def _user(password: str = FIRST) -> str:
    username = f"analyst-{uuid.uuid4().hex[:12]}"
    async with session_factory() as db:
        db.add(User(username=username, password_hash=hash_password(password)))
        await db.commit()
    return username


@asynccontextmanager
async def _browser() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver"
    ) as client:
        yield client


async def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": response.json()["csrf_token"]}


async def _sign_in(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=await _csrf(client),
    )


async def _attempt(username: str, password: str) -> httpx.Response:
    """A sign-in from a browser that is not signed in yet."""
    async with _browser() as client:
        return await _sign_in(client, username, password)


async def test_changing_the_password_needs_the_current_one_and_signs_out_other_browsers() -> None:
    username = await _user()
    async with _browser() as here, _browser() as elsewhere, _browser() as fresh:
        assert (await _sign_in(here, username, FIRST)).status_code == 200
        assert (await _sign_in(elsewhere, username, FIRST)).status_code == 200
        csrf = await _csrf(here)

        wrong = await here.post(
            "/api/v1/auth/password",
            json={"current_password": "not-the-password", "new_password": SECOND},
            headers=csrf,
        )
        assert wrong.status_code == 400
        assert wrong.json()["detail"] == "The current password is not right."
        short = await here.post(
            "/api/v1/auth/password",
            json={"current_password": FIRST, "new_password": "short"},
            headers=csrf,
        )
        assert short.status_code == 422
        unprotected = await here.post(
            "/api/v1/auth/password", json={"current_password": FIRST, "new_password": SECOND}
        )
        assert unprotected.status_code == 403

        changed = await here.post(
            "/api/v1/auth/password",
            json={"current_password": FIRST, "new_password": SECOND},
            headers=csrf,
        )
        assert changed.status_code == 204
        assert (await here.get("/api/v1/auth/me")).status_code == 200
        assert (await elsewhere.get("/api/v1/auth/me")).status_code == 401
        assert (await _sign_in(fresh, username, FIRST)).status_code == 401
        assert (await _sign_in(fresh, username, SECOND)).status_code == 200


async def test_signing_out_every_other_browser_keeps_this_one() -> None:
    username = await _user()
    async with _browser() as here, _browser() as phone, _browser() as laptop:
        for client in (here, phone, laptop):
            assert (await _sign_in(client, username, FIRST)).status_code == 200
        assert (await here.get("/api/v1/auth/sessions")).json() == {"others": 2}

        response = await here.post(
            "/api/v1/auth/sessions/sign-out-others", headers=await _csrf(here)
        )

        assert response.json() == {"signed_out": 2}
        assert (await here.get("/api/v1/auth/me")).status_code == 200
        assert (await phone.get("/api/v1/auth/me")).status_code == 401
        assert (await laptop.get("/api/v1/auth/me")).status_code == 401
        assert (await here.get("/api/v1/auth/sessions")).json() == {"others": 0}


async def test_repeated_failed_sign_ins_hold_the_name_for_a_while() -> None:
    username, neighbour = await _user(), await _user()
    for _ in range(MAX_FAILURES):
        assert (await _attempt(username, "not-the-password")).status_code == 401

    held = await _attempt(username, FIRST)
    assert held.status_code == 429
    assert held.json()["detail"] == "Too many failed sign-ins. Try again in 15 minutes."
    assert int(held.headers["Retry-After"]) == int(LOCKOUT.total_seconds())
    # Another name, and the same name typed differently, are counted on their own and together.
    assert (await _attempt(neighbour, FIRST)).status_code == 200
    assert (await _attempt(f"  {username.upper()} ", FIRST)).status_code == 429

    async with session_factory() as db, db.begin():
        await db.execute(
            update(LoginFailure)
            .where(LoginFailure.username == username)
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (await _attempt(username, FIRST)).status_code == 200
    async with session_factory() as db:
        count = select(LoginFailure).where(LoginFailure.username == username)
        assert await db.scalar(count) is None


async def test_a_successful_sign_in_forgives_earlier_failures() -> None:
    username = await _user()
    for _ in range(MAX_FAILURES - 1):
        await _attempt(username, "not-the-password")
    assert (await _attempt(username, FIRST)).status_code == 200
    for _ in range(MAX_FAILURES - 1):
        assert (await _attempt(username, "not-the-password")).status_code == 401
    assert (await _attempt(username, FIRST)).status_code == 200


async def test_failures_from_long_ago_start_a_new_count() -> None:
    username = await _user()
    for _ in range(MAX_FAILURES - 1):
        await _attempt(username, "not-the-password")
    async with session_factory() as db, db.begin():
        await db.execute(
            update(LoginFailure)
            .where(LoginFailure.username == username)
            .values(first_failed_at=datetime.now(UTC) - WINDOW - timedelta(seconds=1))
        )
    assert (await _attempt(username, "not-the-password")).status_code == 401
    assert (await _attempt(username, FIRST)).status_code == 200


async def test_signing_out_ends_this_session_and_needs_its_csrf_token() -> None:
    username = await _user()
    async with _browser() as here, _browser() as elsewhere:
        assert (await _sign_in(here, username, FIRST)).status_code == 200
        assert (await _sign_in(elsewhere, username, FIRST)).status_code == 200
        cookies = dict(here.cookies)

        assert (await here.post("/api/v1/auth/logout")).status_code == 403
        assert (await here.get("/api/v1/auth/me")).status_code == 200

        response = await here.post("/api/v1/auth/logout", headers=await _csrf(here))

        assert response.status_code == 204
        assert (await here.get("/api/v1/auth/me")).status_code == 401
        # The old cookie no longer works, even sent again by hand; the other browser stays in.
        async with _browser() as replay:
            replay.cookies.update(cookies)
            assert (await replay.get("/api/v1/auth/me")).status_code == 401
        assert (await elsewhere.get("/api/v1/auth/me")).status_code == 200
