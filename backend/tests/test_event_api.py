from typing import Any

from fastapi.routing import APIRoute

from app.auth.routes import current_session
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.routes import router
from app.main import create_app

PREFIX = "/api/v1/events"
PAGED = ("", "/{event_id}/clusters", "/{event_id}/articles", "/{event_id}/timeline")


def _operations() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item for path, item in paths.items() if path.startswith(PREFIX)}


def _query(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}


def test_the_five_event_routes_are_documented_and_read_only() -> None:
    found = _operations()
    assert set(found) == {
        PREFIX,
        f"{PREFIX}/{{event_id}}",
        f"{PREFIX}/{{event_id}}/clusters",
        f"{PREFIX}/{{event_id}}/articles",
        f"{PREFIX}/{{event_id}}/timeline",
    }
    for item in found.values():
        assert set(item) == {"get"}
        headers = [p["name"] for p in item["get"].get("parameters", []) if p["in"] == "header"]
        assert "X-CSRF-Token" not in headers


def test_every_event_route_requires_a_session() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 5
    for route in routes:
        assert current_session in [d.call for d in route.dependant.dependencies], route.path


def test_pages_are_keyset_paged_with_bounded_limits() -> None:
    found = _operations()
    for suffix in PAGED:
        params = _query(found[PREFIX + suffix]["get"])
        assert params["limit"]["schema"]["minimum"] == 1
        assert params["limit"]["schema"]["maximum"] == 100
    for suffix in ("", "/{event_id}/clusters", "/{event_id}/articles"):
        assert "cursor" in _query(found[PREFIX + suffix]["get"])
    assert "after" in _query(found[f"{PREFIX}/{{event_id}}/timeline"]["get"])


def test_the_list_filters_are_the_documented_ones() -> None:
    params = _query(_operations()[PREFIX]["get"])
    assert {"algorithm_version", "status", "country", "entity_id", "from", "to"} <= set(params)
    assert params["status"]["schema"]["anyOf"][0]["enum"] == ["active", "closed", "superseded"]
    country = params["country"]["schema"]["anyOf"][0]
    assert (country["minLength"], country["maxLength"]) == (2, 2)
    # No default in the contract: the server applies the current version, so it can change.
    assert EVENT_ALGORITHM_VERSION not in str(params["algorithm_version"])


def test_a_summary_carries_derived_counts_a_headline_and_top_entities() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    summary = set(schemas["EventSummary"]["properties"])
    assert {
        "id",
        "algorithm_version",
        "status",
        "started_at",
        "ended_at",
        "primary_country",
        "cluster_count",
        "article_count",
        "source_count",
        "headline",
        "headline_article_id",
        "entities",
    } <= summary
    assert {"created_at", "updated_at"} <= set(schemas["EventDetail"]["properties"])
    assert {"score", "signals", "joined_at", "representative_article"} <= set(
        schemas["EventClusterResponse"]["properties"]
    )
    assert {"cluster_id", "provenance"} <= set(schemas["EventArticleResponse"]["properties"])
    assert {"date", "article_count", "source_count", "clusters_started", "evidence"} <= set(
        schemas["EventTimelineDay"]["properties"]
    )
