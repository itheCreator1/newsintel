from typing import Any

from fastapi.routing import APIRoute

from app.auth.routes import current_session
from app.main import create_app
from app.sources.routes import router

PREFIX = "/api/v1/sources"
SUFFIXES = ("", "/coverage", "/timing", "/articles", "/clusters", "/fetches")


def _operations() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item for path, item in paths.items() if path.startswith(PREFIX)}


def _query(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}


def test_the_six_source_routes_are_documented_and_read_only() -> None:
    found = _operations()
    assert set(found) == {PREFIX + "/{source_id}" + suffix for suffix in SUFFIXES}
    for item in found.values():
        assert set(item) == {"get"}
        headers = [p["name"] for p in item["get"].get("parameters", []) if p["in"] == "header"]
        assert "X-CSRF-Token" not in headers


def test_every_source_route_requires_a_session() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 6
    for route in routes:
        assert current_session in [d.call for d in route.dependant.dependencies], route.path


def test_lists_are_keyset_paged_and_metrics_take_a_bounded_window() -> None:
    found = _operations()
    for suffix in ("/articles", "/clusters", "/fetches"):
        params = _query(found[f"{PREFIX}/{{source_id}}{suffix}"]["get"])
        assert "cursor" in params
        assert (params["limit"]["schema"]["minimum"], params["limit"]["schema"]["maximum"]) == (
            1,
            100,
        )
    for suffix in ("", "/coverage", "/timing"):
        days = _query(found[f"{PREFIX}/{{source_id}}{suffix}"]["get"])["days"]["schema"]
        assert (days["minimum"], days["maximum"]) == (1, 366)


def test_the_detail_carries_derived_metrics_with_their_denominators() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    assert {
        "retired_at",
        "first_seen_at",
        "last_seen_at",
        "health",
        "window_days",
        "publishing",
        "extraction",
        "fetches",
        "timeline",
    } <= set(schemas["SourceDetail"]["properties"])
    assert {"articles", "with_published_at"} == set(schemas["SourcePublishing"]["properties"])
    assert {"articles", "extracted", "failed", "in_progress", "not_extracted"} == set(
        schemas["SourceExtraction"]["properties"]
    )
    assert {"stories", "first", "median_minutes_behind", "p90_minutes_behind"} <= set(
        schemas["SourceTiming"]["properties"]
    )
    assert {"source_article_id", "first", "minutes_behind"} <= set(
        schemas["SourceClusterResponse"]["properties"]
    )
