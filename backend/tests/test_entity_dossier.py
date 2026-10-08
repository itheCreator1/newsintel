from app.main import create_app


def test_entity_dossier_routes_are_present_in_openapi() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/v1/entities/{entity_id}" in paths
    assert "/api/v1/entities/{entity_id}/articles" in paths
    assert "/api/v1/entities/{entity_id}/clusters" in paths
    assert "/api/v1/entities/{entity_id}/relationships" in paths


def test_authority_routes_are_documented_and_changes_need_the_csrf_token() -> None:
    paths = create_app().openapi()["paths"]
    changes = {
        ("/api/v1/entities/{entity_id}", "patch"),
        ("/api/v1/entities/{entity_id}/merge", "post"),
        ("/api/v1/entities/{entity_id}/split", "post"),
        ("/api/v1/entities/{entity_id}/distinct/{other_id}", "post"),
        ("/api/v1/entities/{entity_id}/distinct/{other_id}", "delete"),
    }
    reads = {
        ("/api/v1/entities/{entity_id}/variants", "get"),
        ("/api/v1/entities/{entity_id}/history", "get"),
    }
    for path, method in changes | reads:
        operation = paths[path][method]
        headers = [p["name"] for p in operation.get("parameters", []) if p["in"] == "header"]
        assert ("X-CSRF-Token" in headers) is ((path, method) in changes), (path, method)
    dossier = paths["/api/v1/entities/{entity_id}"]["get"]["responses"]["200"]
    assert dossier["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EntityDossierResponse"
    }
    fields = create_app().openapi()["components"]["schemas"]["EntityDossierResponse"]["properties"]
    assert {"redirected_from", "preferred_text", "status", "ambiguous", "note"} <= set(fields)
