from typing import Any

from fastapi.routing import APIRoute

from app.auth.routes import current_session
from app.geo import schemas as geo_schemas
from app.geo.routes import router
from app.main import create_app

PREFIX = "/api/v1/geo"
PATHS = {PREFIX + "/countries", PREFIX + "/articles"}


def _operations() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item for path, item in paths.items() if path.startswith(PREFIX)}


def _query(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}


def test_the_two_geo_routes_are_documented_and_read_only() -> None:
    found = _operations()
    assert set(found) == PATHS
    for item in found.values():
        assert set(item) == {"get"}
        headers = [p["name"] for p in item["get"].get("parameters", []) if p["in"] == "header"]
        assert "X-CSRF-Token" not in headers


def test_every_geo_route_requires_a_session() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 2
    for route in routes:
        assert current_session in [d.call for d in route.dependant.dependencies], route.path


def test_the_map_takes_one_role_a_scope_and_a_bounded_recent_window() -> None:
    params = _query(_operations()[PREFIX + "/countries"]["get"])
    assert params["role"]["schema"]["enum"] == ["story", "mentioned", "source", "event"]
    assert params["role"]["required"] is True
    assert params["scope"]["schema"]["enum"] == ["recent", "investigation"]
    assert params["scope"]["schema"]["default"] == "recent"
    days = params["days"]["schema"]["anyOf"][0]
    assert (days["minimum"], days["maximum"]) == (1, 366)
    assert {"q", "source_country", "story_country", "after", "before"} <= set(params)


def test_evidence_is_keyset_paged_for_the_article_roles_only() -> None:
    params = _query(_operations()[PREFIX + "/articles"]["get"])
    assert params["role"]["schema"]["enum"] == ["story", "mentioned", "source"]
    assert {"code", "days", "cursor", "limit", "scope", "q"} <= set(params)
    assert (params["limit"]["schema"]["minimum"], params["limit"]["schema"]["maximum"]) == (1, 100)


def test_the_map_reports_coverage_with_its_base_and_no_score() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    assert {"unit", "window_total", "located"} <= set(schemas["GeoCoverage"]["properties"])
    assert {"country_code", "articles", "stories", "sources", "events"} <= set(
        schemas["GeoCountry"]["properties"]
    )
    ours = [n for n in schemas if getattr(geo_schemas, n, None) is not None]
    assert {"GeoCountriesResponse", "GeoCountry", "GeoCoverage"} <= set(ours)
    everything = str({n: schemas[n] for n in ours}).lower()
    assert not {"winner", "score", "rank"} & set(everything.replace("'", " ").split())
    assert "skipped_stale" in schemas["GeoArticlePage"]["properties"]
    assert {"stories_estimated", "sources_estimated", "scope", "window_end"} <= set(
        schemas["GeoCountriesResponse"]["properties"]
    )
