"""The Processes page's API: one summary card per background process and one activity list."""

from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.auth.routes import current_session
from app.main import create_app
from app.processes import states
from app.processes.routes import router
from app.processes.summary import PROCESSES

PREFIX = "/api/v1/processes"


def _paths() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item for path, item in paths.items() if path.startswith(PREFIX)}


def _query(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}


def test_fourteen_processes_in_three_groups_in_page_order() -> None:
    assert [(p.key, p.group) for p in PROCESSES] == [
        ("feeds", "per_item"),
        ("articles", "per_item"),
        ("nlp", "per_item"),
        ("clustering", "per_item"),
        ("search", "per_item"),
        ("monitors", "per_item"),
        ("reprocessing", "bulk"),
        ("authority", "bulk"),
        ("rebuild", "bulk"),
        ("source_refresh", "bulk"),
        ("events", "scheduled"),
        ("retention", "scheduled"),
        ("wikidata_refresh", "scheduled"),
        ("wikidata_candidates", "scheduled"),
    ]
    assert all(p.label and p.description for p in PROCESSES)


def test_the_read_routes_are_documented_and_need_a_session() -> None:
    found = _paths()
    assert {f"{PREFIX}", f"{PREFIX}/activity"} <= set(found)
    for path in (f"{PREFIX}", f"{PREFIX}/activity"):
        assert set(found[path]) == {"get"}
    reads = [r for r in router.routes if isinstance(r, APIRoute) and "GET" in r.methods]
    assert len(reads) == 2
    for route in reads:
        assert current_session in [d.call for d in route.dependant.dependencies], route.path


def test_windows_filters_and_page_sizes_are_bounded() -> None:
    found = _paths()
    for path in (f"{PREFIX}", f"{PREFIX}/activity"):
        hours = _query(found[path]["get"])["hours"]["schema"]
        assert (hours["minimum"], hours["maximum"]) == (1, 168), path
    activity = _query(found[f"{PREFIX}/activity"]["get"])
    assert set(activity["status"]["schema"]["enum"]) == {
        "attention", "failed", "running", "queued", "done", "all"
    }  # fmt: skip
    process = activity["process"]["schema"]["anyOf"][0]["enum"]
    assert process == [p.key for p in PROCESSES]
    assert activity["limit"]["schema"]["maximum"] == 100
    assert activity["q"]["schema"]["anyOf"][0]["maxLength"] == 200


@pytest.mark.parametrize(
    ("counts", "state"),
    [
        ({}, "ok"),
        ({"queued": 3}, "working"),
        ({"running": 1}, "working"),
        ({"retrying": 2, "running": 1}, "retrying"),
        ({"lease_expired": 1, "retrying": 2}, "stalled"),
        ({"failed_in_window": 1, "lease_expired": 1}, "failing"),
        # A failure from before the window is still listed, but no longer colours the card.
        ({"failed": 4}, "ok"),
    ],
)
def test_a_per_item_state_follows_failures_leases_retries_and_backlog(
    counts: dict[str, int], state: str
) -> None:
    values = {
        "queued": 0, "running": 0, "retrying": 0, "failed": 0, "lease_expired": 0,
        "failed_in_window": 0, **counts,
    }  # fmt: skip
    assert states.per_item(**values) == state


@pytest.mark.parametrize(
    ("active", "last_failed", "ran", "state"),
    [
        (True, True, True, "working"),
        (False, True, True, "failing"),
        (False, False, True, "ok"),
        (False, False, False, "idle"),
    ],
)
def test_a_run_state_is_working_failing_ok_or_idle(
    active: bool, last_failed: bool, ran: bool, state: str
) -> None:
    assert states.run(active=active, last_failed=last_failed, ran=ran) == state
