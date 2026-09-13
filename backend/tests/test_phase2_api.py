import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.routes import current_session
from app.core.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_feed_and_article_collections_require_authentication() -> None:
    class EmptyDatabase:
        async def scalar(self, _query: object) -> None:
            return None

    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as exc:
        await current_session(request, EmptyDatabase(), Settings())  # type: ignore[arg-type]
    assert exc.value.status_code == 401


def test_phase_two_routes_are_present_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    assert {"get", "post"} <= set(paths["/api/v1/feeds"])
    assert {"get", "patch", "delete"} <= set(paths["/api/v1/feeds/{feed_id}"])
    assert "post" in paths["/api/v1/feeds/{feed_id}/poll"]
    assert "get" in paths["/api/v1/feeds/{feed_id}/fetches"]
    assert "get" in paths["/api/v1/articles"]
    assert "get" in paths["/api/v1/articles/{article_id}"]


def test_phase_three_processing_routes_are_present_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    assert "post" in paths["/api/v1/articles/{article_id}/process"]
    assert "get" in paths["/api/v1/jobs"]
    assert "get" in paths["/api/v1/jobs/{job_id}"]
    assert "post" in paths["/api/v1/jobs/{job_id}/retry"]


def test_mutation_routes_document_csrf_header() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v1/feeds"]["post"]
    headers = [parameter for parameter in operation["parameters"] if parameter["in"] == "header"]
    assert any(parameter["name"] == "X-CSRF-Token" for parameter in headers)
