import os
import uuid

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.db.session import session_factory
from app.investigations.models import SavedSearch
from app.investigations.schemas import InvestigationState, SavedSearchCreate, SavedSearchUpdate
from app.investigations.service import (
    SavedSearchNameConflict,
    create_saved_search,
    get_saved_search,
    list_saved_searches,
    update_saved_search,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _user() -> uuid.UUID:
    async with session_factory() as db, db.begin():
        user = User(username=f"investigator-{uuid.uuid4().hex}", password_hash="unused")
        db.add(user)
        await db.flush()
        return user.id


async def test_saved_search_round_trips_complete_state_as_jsonb() -> None:
    user_id = await _user()
    state = InvestigationState(
        q='"sea level" AND climate',
        source_id=[uuid.uuid4()],
        source_country=["gr"],
        entity_type=["PERSON"],
        after="2026-01-01",  # type: ignore[arg-type]
        sort="oldest",
        interval="week",
    )
    async with session_factory() as db:
        created = await create_saved_search(
            db, user_id, SavedSearchCreate(name="Coastal", state=state)
        )

    async with session_factory() as db:
        loaded = await get_saved_search(db, user_id, created.id)

    assert loaded is not None
    assert loaded.state_version == 1
    assert InvestigationState.model_validate(loaded.state) == state


async def test_saved_search_names_are_unique_per_user_ignoring_case() -> None:
    first_user, second_user = await _user(), await _user()
    async with session_factory() as db:
        await create_saved_search(
            db, first_user, SavedSearchCreate(name="Energy", state=InvestigationState())
        )
        with pytest.raises(SavedSearchNameConflict):
            await create_saved_search(
                db, first_user, SavedSearchCreate(name="energy", state=InvestigationState())
            )
        other = await create_saved_search(
            db, second_user, SavedSearchCreate(name="energy", state=InvestigationState())
        )
        other_id = other.id
        renamed_target = await create_saved_search(
            db, first_user, SavedSearchCreate(name="Elections", state=InvestigationState())
        )
        renamed_id = renamed_target.id
        with pytest.raises(SavedSearchNameConflict):
            await update_saved_search(db, renamed_target, SavedSearchUpdate(name="ENERGY"))

    async with session_factory() as db:
        assert await get_saved_search(db, first_user, other_id) is None
        reloaded = await get_saved_search(db, first_user, renamed_id)
    assert reloaded is not None and reloaded.name == "Elections"


async def test_saved_searches_page_by_name_without_offsets() -> None:
    user_id = await _user()
    async with session_factory() as db:
        for name in ["Delta", "alpha", "Charlie", "Bravo", "Echo"]:
            await create_saved_search(
                db, user_id, SavedSearchCreate(name=name, state=InvestigationState())
            )

    names: list[str] = []
    cursor: str | None = None
    async with session_factory() as db:
        while True:
            rows, cursor = await list_saved_searches(db, user_id, cursor, 2)
            names.extend(row.name for row in rows)
            if cursor is None:
                break

    assert names == ["Bravo", "Charlie", "Delta", "Echo", "alpha"]


async def test_updating_state_replaces_it_and_deleting_the_user_cascades() -> None:
    user_id = await _user()
    async with session_factory() as db:
        item = await create_saved_search(
            db, user_id, SavedSearchCreate(name="Draft", state=InvestigationState(q="energy"))
        )
        updated = await update_saved_search(
            db, item, SavedSearchUpdate(state=InvestigationState(q="grid", sort="newest"))
        )
    assert updated.state["q"] == "grid" and updated.state["sort"] == "newest"
    assert updated.name == "Draft"

    async with session_factory() as db, db.begin():
        user = await db.get(User, user_id)
        await db.delete(user)
    async with session_factory() as db:
        remaining = await db.scalar(select(SavedSearch).where(SavedSearch.id == item.id))
    assert remaining is None
