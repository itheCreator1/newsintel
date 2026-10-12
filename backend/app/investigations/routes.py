import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.investigations.listing import (
    InvalidInvestigationCursor,
    InvestigationOrder,
    InvestigationPage,
    UnwatchableSavedSearch,
    WatchNameTaken,
    list_investigations,
    watch_saved_search,
)
from app.investigations.models import SavedSearch
from app.investigations.schemas import (
    SavedSearchCreate,
    SavedSearchPage,
    SavedSearchResponse,
    SavedSearchUpdate,
)
from app.investigations.service import (
    InvalidSavedSearchCursor,
    SavedSearchNameConflict,
    create_saved_search,
    delete_saved_search,
    get_saved_search,
    list_saved_searches,
    saved_search_response,
    update_saved_search,
)
from app.monitors.schemas import MonitorResponse
from app.monitors.service import monitor_response

router = APIRouter(tags=["investigations"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


async def _owned(db: AsyncSession, session: Session, saved_search_id: uuid.UUID) -> SavedSearch:
    item = await get_saved_search(db, session.user_id, saved_search_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Saved search not found")
    return item


@router.get("/saved-searches", response_model=SavedSearchPage)
async def list_searches(
    db: Db,
    session: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SavedSearchPage:
    try:
        rows, next_cursor = await list_saved_searches(db, session.user_id, cursor, limit)
    except InvalidSavedSearchCursor as exc:
        raise HTTPException(422, str(exc)) from exc
    return SavedSearchPage(
        items=[saved_search_response(item) for item in rows], next_cursor=next_cursor
    )


@router.post(
    "/saved-searches", response_model=SavedSearchResponse, status_code=status.HTTP_201_CREATED
)
async def create_search(
    payload: SavedSearchCreate, db: Db, session: Mutation
) -> SavedSearchResponse:
    try:
        item = await create_saved_search(db, session.user_id, payload)
    except SavedSearchNameConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return saved_search_response(item)


@router.get("/saved-searches/{saved_search_id}", response_model=SavedSearchResponse)
async def get_search(saved_search_id: uuid.UUID, db: Db, session: Auth) -> SavedSearchResponse:
    return saved_search_response(await _owned(db, session, saved_search_id))


@router.patch("/saved-searches/{saved_search_id}", response_model=SavedSearchResponse)
async def update_search(
    saved_search_id: uuid.UUID, payload: SavedSearchUpdate, db: Db, session: Mutation
) -> SavedSearchResponse:
    item = await _owned(db, session, saved_search_id)
    try:
        item = await update_saved_search(db, item, payload)
    except SavedSearchNameConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return saved_search_response(item)


@router.delete("/saved-searches/{saved_search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search(saved_search_id: uuid.UUID, db: Db, session: Mutation) -> Response:
    await delete_saved_search(db, await _owned(db, session, saved_search_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/saved-searches/{saved_search_id}/watch", response_model=MonitorResponse)
async def watch_search(saved_search_id: uuid.UUID, db: Db, session: Mutation) -> MonitorResponse:
    """Watch the saved search in place: one monitor per saved search, resumed if paused."""
    item = await _owned(db, session, saved_search_id)
    try:
        monitor = await watch_saved_search(db, item)
    except UnwatchableSavedSearch as exc:
        raise HTTPException(422, str(exc)) from exc
    except WatchNameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return monitor_response(monitor)


@router.get("/investigations", response_model=InvestigationPage)
async def list_all_investigations(
    db: Db,
    session: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    order: InvestigationOrder = "name",
) -> InvestigationPage:
    """Saved searches and monitors together; a saved search watched in place is one item."""
    try:
        return await list_investigations(db, session.user_id, cursor, limit, order)
    except InvalidInvestigationCursor as exc:
        raise HTTPException(422, str(exc)) from exc
