import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.events import queries
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event
from app.events.schemas import (
    EventArticlePage,
    EventClusterPage,
    EventDetail,
    EventPage,
    EventTimelinePage,
)
from app.feeds.service import decode_cursor

router = APIRouter(tags=["events"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Limit = Annotated[int, Query(ge=1, le=100)]


async def _event_or_404(db: AsyncSession, event_id: uuid.UUID) -> Event:
    event = await queries.get_event(db, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


def _cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


@router.get("/events", response_model=EventPage)
async def list_events(
    db: Db,
    _auth: Auth,
    algorithm_version: Annotated[
        str | None,
        Query(description="Defaults to the current algorithm version, so versions never mix."),
    ] = None,
    status: Literal["active", "closed", "superseded"] | None = None,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    entity_id: uuid.UUID | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    cursor: str | None = None,
    limit: Limit = 30,
) -> EventPage:
    return await queries.events(
        db,
        version=algorithm_version or EVENT_ALGORITHM_VERSION,
        status=status,
        country=country,
        entity_id=entity_id,
        start=from_,
        end=to,
        limit=limit,
        cursor=_cursor_or_400(cursor),
    )


@router.get("/events/{event_id}", response_model=EventDetail)
async def get_event(event_id: uuid.UUID, db: Db, _auth: Auth) -> EventDetail:
    return await queries.detail(db, await _event_or_404(db, event_id))


@router.get("/events/{event_id}/clusters", response_model=EventClusterPage)
async def get_event_clusters(
    event_id: uuid.UUID, db: Db, _auth: Auth, cursor: str | None = None, limit: Limit = 30
) -> EventClusterPage:
    await _event_or_404(db, event_id)
    return await queries.clusters(db, event_id, limit, _cursor_or_400(cursor))


@router.get("/events/{event_id}/articles", response_model=EventArticlePage)
async def get_event_articles(
    event_id: uuid.UUID, db: Db, _auth: Auth, cursor: str | None = None, limit: Limit = 30
) -> EventArticlePage:
    await _event_or_404(db, event_id)
    return await queries.articles(db, event_id, limit, _cursor_or_400(cursor))


@router.get("/events/{event_id}/timeline", response_model=EventTimelinePage)
async def get_event_timeline(
    event_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    after: Annotated[
        date | None, Query(description="Return only days after this UTC date.")
    ] = None,
    limit: Limit = 30,
) -> EventTimelinePage:
    await _event_or_404(db, event_id)
    return await queries.timeline(db, event_id, limit, after)
