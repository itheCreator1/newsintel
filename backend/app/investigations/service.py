import base64
import binascii
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigations.models import SavedSearch
from app.investigations.schemas import (
    STATE_VERSION,
    InvestigationState,
    SavedSearchCreate,
    SavedSearchResponse,
    SavedSearchUpdate,
)


class SavedSearchNameConflict(Exception):
    pass


class InvalidSavedSearchCursor(ValueError):
    pass


def _encode_cursor(item: SavedSearch) -> str:
    return base64.urlsafe_b64encode(json.dumps([item.name, str(item.id)]).encode()).decode()


def _decode_cursor(value: str) -> tuple[str, uuid.UUID]:
    try:
        name, item_id = json.loads(base64.urlsafe_b64decode(value.encode()))
        return str(name), uuid.UUID(item_id)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidSavedSearchCursor("Invalid saved search cursor") from exc


def saved_search_response(item: SavedSearch) -> SavedSearchResponse:
    state: InvestigationState | None = None
    problem: str | None = None
    if item.state_version != STATE_VERSION:
        problem = f"Saved search format version {item.state_version} is not supported"
    else:
        try:
            state = InvestigationState.model_validate(item.state)
        except ValidationError as exc:
            problem = "; ".join(error["msg"] for error in exc.errors())
    return SavedSearchResponse(
        id=item.id,
        name=item.name,
        state_version=item.state_version,
        state=state,
        problem=problem,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def list_saved_searches(
    db: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[SavedSearch], str | None]:
    query = (
        select(SavedSearch)
        .where(SavedSearch.user_id == user_id)
        .order_by(SavedSearch.name, SavedSearch.id)
    )
    if cursor:
        name, item_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                SavedSearch.name > name,
                and_(SavedSearch.name == name, SavedSearch.id > item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = _encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    return rows[:limit], next_cursor


async def get_saved_search(
    db: AsyncSession, user_id: uuid.UUID, saved_search_id: uuid.UUID
) -> SavedSearch | None:
    item: SavedSearch | None = await db.scalar(
        select(SavedSearch).where(SavedSearch.id == saved_search_id, SavedSearch.user_id == user_id)
    )
    return item


async def _commit(db: AsyncSession, item: SavedSearch) -> SavedSearch:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise SavedSearchNameConflict("A saved search with this name already exists") from exc
    await db.refresh(item)
    return item


async def create_saved_search(
    db: AsyncSession, user_id: uuid.UUID, payload: SavedSearchCreate
) -> SavedSearch:
    item = SavedSearch(
        user_id=user_id,
        name=payload.name,
        state_version=STATE_VERSION,
        state=payload.state.model_dump(mode="json"),
    )
    db.add(item)
    return await _commit(db, item)


async def update_saved_search(
    db: AsyncSession, item: SavedSearch, payload: SavedSearchUpdate
) -> SavedSearch:
    if payload.name is not None:
        item.name = payload.name
    if payload.state is not None:
        item.state_version = STATE_VERSION
        item.state = payload.state.model_dump(mode="json")
    return await _commit(db, item)
