import subprocess
import sys
import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.investigations.schemas import STATE_VERSION, InvestigationState
from app.monitors.models import Monitor
from app.monitors.schemas import MonitorCreate, MonitorUpdate
from app.monitors.service import (
    InvalidMonitorCursor,
    _decode_cursor,
    _encode_cursor,
    monitor_response,
)

ID = str(uuid.uuid4())


def _stored(
    state: dict[str, object], version: int = STATE_VERSION, kind: str = "search"
) -> Monitor:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    return Monitor(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Grid",
        kind=kind,
        state_version=version,
        state=state,
        enabled=True,
        unseen_article_count=0,
        unseen_cluster_count=0,
        next_evaluation_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("kind", "state"),
    [
        ("search", {"q": "grid"}),
        ("search", {"story_country": ["GR"]}),
        ("search", {"after": "2026-01-01"}),
        ("entity", {"entity_id": [ID]}),
        ("entity", {"entity_id": [ID], "q": "energy"}),
        ("source", {"source_id": [ID]}),
        ("country", {"story_country": ["gr"]}),
        ("country", {"mentioned_country": ["DE"]}),
        ("country", {"source_country": ["US"]}),
        ("cluster", {"story_cluster_id": [ID]}),
    ],
)
def test_each_kind_accepts_a_state_that_names_its_target(
    kind: str, state: dict[str, object]
) -> None:
    monitor = MonitorCreate.model_validate({"name": "Watch", "kind": kind, "state": state})

    assert monitor.kind == kind


@pytest.mark.parametrize(
    ("kind", "state"),
    [
        ("search", {}),
        ("search", {"sort": "newest", "interval": "week"}),
        ("entity", {"q": "energy"}),
        ("source", {"entity_id": [ID]}),
        ("country", {"q": "energy"}),
        ("cluster", {}),
        ("cluster", {"story_cluster_id": [ID, str(uuid.uuid4())]}),
        ("cluster", {"q": "energy"}),
        ("search", {"q": '"unterminated'}),
    ],
)
def test_each_kind_rejects_a_state_without_its_target(kind: str, state: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MonitorCreate.model_validate({"name": "Watch", "kind": kind, "state": state})


def test_unknown_kind_and_unknown_state_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        MonitorCreate.model_validate({"name": "Watch", "kind": "topic", "state": {"q": "a"}})
    with pytest.raises(ValidationError):
        MonitorCreate.model_validate(
            {"name": "Watch", "kind": "search", "state": {"q": "a", "page": 2}}
        )


def test_monitor_name_is_trimmed_and_required() -> None:
    state = InvestigationState(q="grid")

    assert MonitorCreate(name="  Grid  ", kind="search", state=state).name == "Grid"
    with pytest.raises(ValidationError):
        MonitorCreate(name="   ", kind="search", state=state)


def test_update_changes_kind_and_state_together_or_not_at_all() -> None:
    assert MonitorUpdate(name="Renamed", enabled=False).state is None
    assert MonitorUpdate(kind="search", state=InvestigationState(q="grid")).kind == "search"
    with pytest.raises(ValidationError):
        MonitorUpdate(state=InvestigationState(q="grid"))
    with pytest.raises(ValidationError):
        MonitorUpdate(kind="search")
    with pytest.raises(ValidationError):
        MonitorUpdate(kind="entity", state=InvestigationState(q="grid"))


def test_create_state_survives_a_json_round_trip() -> None:
    monitor = MonitorCreate.model_validate(
        {"name": "Watch", "kind": "search", "state": {"q": "grid", "after": "2026-01-01"}}
    )
    stored = monitor.state.model_dump(mode="json")

    assert stored["after"] == "2026-01-01"
    assert InvestigationState.model_validate(stored).after == date(2026, 1, 1)


def test_response_reports_a_stored_state_that_no_longer_validates() -> None:
    valid = monitor_response(_stored({"q": "grid"}))
    invalid = monitor_response(_stored({"q": "grid", "retired_filter": True}))
    future = monitor_response(_stored({"q": "grid"}, version=STATE_VERSION + 1))
    wrong_target = monitor_response(_stored({"q": "grid"}, kind="entity"))

    assert valid.state is not None and valid.problem is None and valid.kind == "search"
    assert invalid.state is None and invalid.problem
    assert future.state is None and "not supported" in (future.problem or "")
    assert wrong_target.state is None and wrong_target.problem


def test_response_carries_evaluation_and_unseen_state() -> None:
    item = _stored({"q": "grid"})
    item.unseen_article_count = 3
    item.unseen_cluster_count = 1
    item.error_category = "search_unavailable"

    response = monitor_response(item)

    assert (response.unseen_article_count, response.unseen_cluster_count) == (3, 1)
    assert response.enabled is True and response.error_category == "search_unavailable"
    assert response.latest_match_at is None and response.last_evaluated_at is None


def test_cursor_round_trips_and_rejects_malformed_values() -> None:
    item = _stored({})
    assert _decode_cursor(_encode_cursor(item)) == (item.name, item.id)
    for value in ["a", "W10=", "WyJ4IiwgIm5vcGUiXQ=="]:
        with pytest.raises(InvalidMonitorCursor):
            _decode_cursor(value)


@pytest.mark.parametrize(
    "entry_point", ["app.monitors.models", "app.scheduler", "app.jobs.monitors"]
)
def test_monitor_foreign_keys_resolve_in_processes_that_never_import_auth(entry_point: str) -> None:
    # The scheduler and worker load monitors without the API's auth imports; a fresh interpreter is
    # the only way to see that (this pytest process has already imported every model).
    code = (
        f"import {entry_point}\n"
        "from app.monitors.models import Monitor\n"
        "for key in Monitor.__table__.foreign_keys: key.column\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-800:]
