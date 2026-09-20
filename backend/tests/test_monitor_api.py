import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import create_app
from app.monitors.models import Monitor
from app.monitors.results import (
    decode_results_cursor,
    encode_results_cursor,
    results_body,
)
from app.monitors.schemas import MonitorViewed
from app.monitors.service import (
    InvalidMonitorCursor,
    _decode_activity_cursor,
    _encode_activity_cursor,
)
from app.search.routes import search_result

UPTO = datetime(2026, 9, 20, 12, tzinfo=UTC)
AFTER = UPTO - timedelta(hours=1)
SECRET = "test-secret"


def _monitor(latest: datetime | None) -> Monitor:
    return Monitor(id=uuid.uuid4(), name="Grid", latest_match_at=latest)


def test_monitor_routes_are_documented_and_mutations_require_csrf() -> None:
    paths = create_app().openapi()["paths"]
    collection = paths["/api/v1/monitors"]
    item = paths["/api/v1/monitors/{monitor_id}"]
    viewed = paths["/api/v1/monitors/{monitor_id}/viewed"]
    found = paths["/api/v1/monitors/{monitor_id}/results"]
    assert {"get", "post"} <= set(collection) and {"get", "patch", "delete"} <= set(item)
    assert "post" in viewed and "get" in found

    def headers(operation: dict[str, Any]) -> list[str]:
        return [p["name"] for p in operation.get("parameters", []) if p["in"] == "header"]

    for operation in (collection["post"], item["patch"], item["delete"], viewed["post"]):
        assert "X-CSRF-Token" in headers(operation)
    for operation in (collection["get"], item["get"], found["get"]):
        assert "X-CSRF-Token" not in headers(operation)


def test_viewed_needs_the_boundary_the_analyst_saw() -> None:
    with pytest.raises(ValidationError):
        MonitorViewed.model_validate({})
    assert MonitorViewed.model_validate({"through": UPTO.isoformat()}).through == UPTO


def test_activity_cursor_round_trips_with_and_without_a_latest_match() -> None:
    for latest in (UPTO, None):
        item = _monitor(latest)
        assert _decode_activity_cursor(_encode_activity_cursor(item)) == (latest, item.id)
    for value in ["a", "W10=", "WyJ4IiwgIm5vcGUiXQ=="]:
        with pytest.raises(InvalidMonitorCursor):
            _decode_activity_cursor(value)


def test_results_body_covers_only_the_window_newest_first_without_highlights() -> None:
    body = results_body({"match_all": {}}, AFTER, UPTO, 5, None)

    bounds = body["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]
    assert bounds == {"gt": AFTER.isoformat(), "lte": UPTO.isoformat()}
    assert body["size"] == 5 and "highlight" not in body and "search_after" not in body
    assert body["sort"] == [{"first_discovered_at": "desc"}, {"article_id": "desc"}]
    paged = results_body({"match_all": {}}, None, UPTO, 5, [1, "a"])
    assert paged["search_after"] == [1, "a"]
    assert "gt" not in paged["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]


def test_results_cursor_pins_the_window_and_rejects_other_owners() -> None:
    monitor_id, session_id = uuid.uuid4(), uuid.uuid4()
    value = encode_results_cursor(SECRET, session_id, monitor_id, "unseen", AFTER, UPTO, [7, "z"])

    assert decode_results_cursor(SECRET, value, session_id, monitor_id, "unseen") == (
        AFTER,
        UPTO,
        [7, "z"],
    )
    for args in [
        (SECRET, value, uuid.uuid4(), monitor_id, "unseen"),
        (SECRET, value, session_id, uuid.uuid4(), "unseen"),
        (SECRET, value, session_id, monitor_id, "recent"),
        ("other-secret", value, session_id, monitor_id, "unseen"),
        (SECRET, value[:-4] + "AAAA", session_id, monitor_id, "unseen"),
    ]:
        with pytest.raises(HTTPException) as caught:
            decode_results_cursor(*args)
        assert caught.value.status_code == 422
    open_start = encode_results_cursor(SECRET, session_id, monitor_id, "recent", None, UPTO, [])
    assert decode_results_cursor(SECRET, open_start, session_id, monitor_id, "recent")[0] is None


def test_search_result_maps_a_hit_without_highlights() -> None:
    article_id, source_id, cluster_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    hit = {
        "_source": {
            "article_id": article_id,
            "title": "Grid failure",
            "effective_date": UPTO.isoformat(),
            "distinct_source_count": 1,
            "provenance": [{"source_id": source_id, "source_name": "Wire", "source_country": "GR"}],
            "primary_story_country": "GR",
            "story_cluster_id": cluster_id,
            "cluster_source_count": 3,
        }
    }

    result = search_result(hit)

    assert str(result.article_id) == article_id and result.sources == ["Wire"]
    assert result.source_refs[0].country == "GR"
    assert result.story_cluster is not None and result.story_cluster.source_count == 3
    assert result.summary is None and result.highlights == []
