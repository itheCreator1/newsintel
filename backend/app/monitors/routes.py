import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.monitors.changes import monitor_changes
from app.monitors.models import Monitor
from app.monitors.results import Scope, monitor_results
from app.monitors.schemas import (
    MonitorChanges,
    MonitorCreate,
    MonitorPage,
    MonitorResponse,
    MonitorResultPage,
    MonitorUpdate,
    MonitorViewed,
)
from app.monitors.service import (
    InvalidMonitorCursor,
    MonitorNameConflict,
    NotYetEvaluated,
    ViewedBeyondEvaluation,
    create_monitor,
    get_monitor,
    list_monitors,
    mark_viewed,
    monitor_response,
    update_monitor,
)

router = APIRouter(tags=["monitors"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


async def _owned(
    db: AsyncSession, session: Session, monitor_id: uuid.UUID, *, lock: bool = False
) -> Monitor:
    item = await get_monitor(db, session.user_id, monitor_id, lock=lock)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitor not found")
    return item


@router.get("/monitors", response_model=MonitorPage)
async def list_all(
    db: Db,
    session: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    order: Literal["name", "activity"] = "name",
) -> MonitorPage:
    try:
        rows, next_cursor = await list_monitors(db, session.user_id, cursor, limit, order)
    except InvalidMonitorCursor as exc:
        raise HTTPException(422, str(exc)) from exc
    return MonitorPage(items=[monitor_response(item) for item in rows], next_cursor=next_cursor)


@router.post("/monitors", response_model=MonitorResponse, status_code=status.HTTP_201_CREATED)
async def create(payload: MonitorCreate, db: Db, session: Mutation) -> MonitorResponse:
    try:
        return monitor_response(await create_monitor(db, session.user_id, payload))
    except MonitorNameConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/monitors/{monitor_id}", response_model=MonitorResponse)
async def get_one(monitor_id: uuid.UUID, db: Db, session: Auth) -> MonitorResponse:
    return monitor_response(await _owned(db, session, monitor_id))


@router.patch("/monitors/{monitor_id}", response_model=MonitorResponse)
async def update(
    monitor_id: uuid.UUID, payload: MonitorUpdate, db: Db, session: Mutation
) -> MonitorResponse:
    # Locked, so a criteria reset sees and clears what a concurrent evaluation just wrote.
    item = await _owned(db, session, monitor_id, lock=True)
    try:
        return monitor_response(await update_monitor(db, item, payload))
    except MonitorNameConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.delete("/monitors/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(monitor_id: uuid.UUID, db: Db, session: Mutation) -> Response:
    await db.delete(await _owned(db, session, monitor_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/monitors/{monitor_id}/results", response_model=MonitorResultPage)
async def results(
    monitor_id: uuid.UUID,
    db: Db,
    session: Auth,
    scope: Scope = "unseen",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> MonitorResultPage:
    item = await _owned(db, session, monitor_id)
    return await monitor_results(db, item, session.id, scope, limit, cursor)


@router.get("/monitors/{monitor_id}/changes", response_model=MonitorChanges)
async def changes(monitor_id: uuid.UUID, db: Db, session: Auth) -> MonitorChanges:
    return await monitor_changes(db, await _owned(db, session, monitor_id))


@router.post("/monitors/{monitor_id}/viewed", response_model=MonitorResponse)
async def viewed(
    monitor_id: uuid.UUID, payload: MonitorViewed, db: Db, session: Mutation
) -> MonitorResponse:
    item = await _owned(db, session, monitor_id, lock=True)
    try:
        return monitor_response(await mark_viewed(db, item, payload.through))
    except NotYetEvaluated as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ViewedBeyondEvaluation as exc:
        raise HTTPException(422, str(exc)) from exc
