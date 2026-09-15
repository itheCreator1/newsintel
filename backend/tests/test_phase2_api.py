import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.routes import current_session
from app.core.config import Settings
from app.main import create_app
from app.nlp.routes import _capabilities, _lookup_cursor


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


def test_phase_five_annotation_routes_are_present_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    assert "get" in paths["/api/v1/articles/{article_id}/annotations"]
    assert "post" in paths["/api/v1/articles/{article_id}/nlp/reprocess"]
    assert "get" in paths["/api/v1/nlp/status"]
    assert "get" in paths["/api/v1/nlp/failures"]
    assert "post" in paths["/api/v1/nlp/jobs/{job_id}/retry"]
    assert {"get", "put"} <= set(paths["/api/v1/nlp/stop-words"])
    assert "get" in paths["/api/v1/nlp/entities"]
    assert "get" in paths["/api/v1/nlp/keywords"]


def test_enabled_ner_reports_a_missing_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.nlp.routes.importlib.util.find_spec",
        lambda name: object() if name == "spacy" else None,
    )
    monkeypatch.setattr("app.nlp.routes.importlib.metadata.version", lambda _name: "3.8.16")

    capabilities = _capabilities(Settings(nlp_ner_enabled=True, nlp_ner_model="missing_model"))

    entities = next(item for item in capabilities if item.name == "entities")
    assert entities.state == "configuration_failure"
    assert entities.detail == "spaCy model 'missing_model' is not installed"


def test_annotation_lookup_rejects_malformed_base64_cursor() -> None:
    with pytest.raises(HTTPException) as exc:
        _lookup_cursor("a")

    assert exc.value.status_code == 422
