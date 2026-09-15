import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.investigations.models import SavedSearch
from app.investigations.schemas import STATE_VERSION, InvestigationState, SavedSearchCreate
from app.investigations.service import (
    InvalidSavedSearchCursor,
    _decode_cursor,
    _encode_cursor,
    saved_search_response,
)
from app.main import create_app


def _stored(state: dict[str, object], version: int = STATE_VERSION) -> SavedSearch:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    return SavedSearch(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Energy",
        state_version=version,
        state=state,
        created_at=now,
        updated_at=now,
    )


def test_investigation_state_normalizes_codes_and_query() -> None:
    state = InvestigationState(
        q="  climate AND energy  ",
        source_country=["gr"],
        story_country=[" de "],
        language=["EN"],
        after=date(2026, 1, 1),
        before=date(2026, 2, 1),
        interval="week",
    )

    assert state.q == "climate AND energy"
    assert state.source_country == ["GR"]
    assert state.story_country == ["DE"]
    assert state.language == ["en"]
    assert state.model_dump(mode="json")["after"] == "2026-01-01"


@pytest.mark.parametrize(
    "state",
    [
        {"q": '"unterminated'},
        {"q": "title:unsupported"},
        {"source_country": ["GRC"]},
        {"after": "2026-02-01", "before": "2026-01-01"},
        {"sort": "random"},
        {"interval": "minute"},
        {"page": 2},
        {"entity_id": ["not-a-uuid"]},
    ],
)
def test_investigation_state_rejects_values_search_would_reject(state: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InvestigationState.model_validate(state)


def test_saved_search_name_is_trimmed_and_required() -> None:
    assert SavedSearchCreate(name="  Energy  ", state=InvestigationState()).name == "Energy"
    with pytest.raises(ValidationError):
        SavedSearchCreate(name="   ", state=InvestigationState())


def test_response_reports_a_stored_state_that_no_longer_validates() -> None:
    valid = saved_search_response(_stored({"q": "energy", "sort": "newest"}))
    invalid = saved_search_response(_stored({"q": "energy", "retired_filter": True}))
    future = saved_search_response(_stored({"q": "energy"}, version=STATE_VERSION + 1))

    assert valid.state is not None and valid.state.sort == "newest" and valid.problem is None
    assert invalid.state is None and invalid.problem
    assert future.state is None and "not supported" in (future.problem or "")


def test_cursor_round_trips_and_rejects_malformed_values() -> None:
    item = _stored({})
    assert _decode_cursor(_encode_cursor(item)) == (item.name, item.id)
    for value in ["a", "W10=", "WyJ4IiwgIm5vcGUiXQ=="]:
        with pytest.raises(InvalidSavedSearchCursor):
            _decode_cursor(value)


def test_saved_search_routes_require_csrf_for_mutations() -> None:
    paths = create_app().openapi()["paths"]
    collection, item = (
        paths["/api/v1/saved-searches"],
        paths["/api/v1/saved-searches/{saved_search_id}"],
    )
    assert {"get", "post"} <= set(collection)
    assert {"get", "patch", "delete"} <= set(item)
    for operation in (collection["post"], item["patch"], item["delete"]):
        headers = [param["name"] for param in operation["parameters"] if param["in"] == "header"]
        assert "X-CSRF-Token" in headers
    for operation in (collection["get"], item["get"]):
        headers = [
            param["name"] for param in operation.get("parameters", []) if param["in"] == "header"
        ]
        assert "X-CSRF-Token" not in headers
