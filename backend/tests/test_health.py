import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_external_services() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_api_lifespan_opens_and_closes_the_request_pool() -> None:
    """ASGITransport skips the lifespan, so the other API tests only see the pool-less path."""
    from app.db import session as db_session

    app = create_app()
    assert db_session._request_maker is None
    async with app.router.lifespan_context(app):
        engine = db_session._request_engine
        assert engine is not None and db_session._request_maker is not None
        # Requests take their session from the pool, not from a NullPool engine per thread.
        requests = db_session.get_db()
        session = await anext(requests)
        assert session.bind is engine
        await requests.aclose()
    assert db_session._request_engine is None
    assert db_session._request_maker is None
