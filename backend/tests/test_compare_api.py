from typing import Any

from fastapi.routing import APIRoute

from app.auth.routes import current_session
from app.compare import schemas as compare_schemas
from app.compare.routes import router
from app.main import create_app

PREFIX = "/api/v1/compare"
SUFFIXES = ("", "/articles", "/stories")


def _operations() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item for path, item in paths.items() if path.startswith(PREFIX)}


def _query(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}


def test_the_three_compare_routes_are_documented_and_read_only() -> None:
    found = _operations()
    assert set(found) == {PREFIX + suffix for suffix in SUFFIXES}
    for item in found.values():
        assert set(item) == {"get"}
        headers = [p["name"] for p in item["get"].get("parameters", []) if p["in"] == "header"]
        assert "X-CSRF-Token" not in headers


def test_every_compare_route_requires_a_session() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 3
    for route in routes:
        assert current_session in [d.call for d in route.dependant.dependencies], route.path


def test_every_route_takes_the_same_subjects_and_a_bounded_window() -> None:
    for suffix in SUFFIXES:
        params = _query(_operations()[PREFIX + suffix]["get"])
        assert {"kind", "a", "b", "role", "days"} <= set(params), suffix
        assert params["kind"]["schema"]["enum"] == ["entity", "source", "country"]
        assert params["days"]["schema"]["minimum"] == 1
        assert params["days"]["schema"]["maximum"] == 366


def test_evidence_is_keyset_paged_by_part_with_bounded_limits() -> None:
    for suffix in ("/articles", "/stories"):
        params = _query(_operations()[PREFIX + suffix]["get"])
        assert params["part"]["schema"]["enum"] == ["a", "both", "b"]
        assert params["part"]["required"] is True
        assert "cursor" in params
        assert (params["limit"]["schema"]["minimum"], params["limit"]["schema"]["maximum"]) == (
            1,
            100,
        )
    assert "part" not in _query(_operations()[PREFIX]["get"])


def test_the_summary_reports_overlap_with_a_denominator_and_no_score() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    assert {"only_a", "both", "only_b", "union", "jaccard"} <= set(schemas["Overlap"]["properties"])
    assert {"articles", "stories", "sources"} == set(schemas["CompareOverlap"]["properties"])
    assert {"subject", "articles", "stories", "sources", "timeline"} <= set(
        schemas["CompareSide"]["properties"]
    )
    assert {"a_articles", "b_articles"} <= set(schemas["CompareClusterResponse"]["properties"])
    ours = [n for n in schemas if getattr(compare_schemas, n, None) is not None]
    assert {"CompareResponse", "Overlap", "RelatedItem"} <= set(ours)
    everything = str({n: schemas[n] for n in ours}).lower()
    assert "winner" not in everything and "score" not in everything
