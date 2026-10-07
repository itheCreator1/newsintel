"""A malformed keyset cursor is the caller's mistake: every paged route answers 400, never 500."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.cursors import cursor_or_400
from app.auth.routes import current_session
from app.db.session import get_db
from app.feeds.service import encode_cursor
from app.main import create_app

MALFORMED = ("bad", "!!!", "Zm9v", "bm90LWEtZGF0ZXxub3QtYS11dWlk", "/w==")


def test_cursor_or_400_passes_none_through() -> None:
    assert cursor_or_400(None) is None


def test_cursor_or_400_decodes_what_encode_cursor_made() -> None:
    created, item_id = datetime(2026, 10, 7, 9, 30, tzinfo=UTC), uuid.uuid4()
    assert cursor_or_400(encode_cursor(created, item_id)) == (created, item_id)


@pytest.mark.parametrize("cursor", MALFORMED)
def test_cursor_or_400_answers_400_for_a_malformed_cursor(cursor: str) -> None:
    with pytest.raises(HTTPException) as raised:
        cursor_or_400(cursor)
    assert raised.value.status_code == 400


class _NoQueries:
    """Stands in for the database: finds any parent row, and fails if a page query runs."""

    async def get(self, *_: object) -> object:
        return SimpleNamespace()

    async def scalar(self, *_: object) -> object:
        return SimpleNamespace()

    async def scalars(self, *_: object) -> object:
        raise AssertionError("the route queried with a malformed cursor")

    async def execute(self, *_: object) -> object:
        raise AssertionError("the route queried with a malformed cursor")


@pytest.mark.parametrize(
    "path",
    [
        "/feeds",
        f"/feeds/{uuid.uuid4()}/fetches",
        "/articles",
        "/jobs",
        "/clustering/failures",
        f"/clusters/{uuid.uuid4()}",
        "/nlp/failures",
        "/search/indexing/failures",
    ],
)
@pytest.mark.parametrize("cursor", MALFORMED)
@pytest.mark.asyncio
async def test_paged_routes_answer_400_for_a_malformed_cursor(path: str, cursor: str) -> None:
    async def no_queries() -> AsyncIterator[object]:
        yield _NoQueries()

    app = create_app()
    app.dependency_overrides[get_db] = no_queries
    app.dependency_overrides[current_session] = lambda: SimpleNamespace()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/api/v1{path}", params={"cursor": cursor})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid cursor"
