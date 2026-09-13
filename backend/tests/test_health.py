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
