from app.main import create_app


def test_entity_dossier_routes_are_present_in_openapi() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/v1/entities/{entity_id}" in paths
    assert "/api/v1/entities/{entity_id}/articles" in paths
    assert "/api/v1/entities/{entity_id}/clusters" in paths
    assert "/api/v1/entities/{entity_id}/relationships" in paths
