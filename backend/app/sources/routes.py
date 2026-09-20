import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.feeds.models import Feed
from app.feeds.schemas import FetchPage
from app.feeds.service import decode_cursor
from app.sources import queries
from app.sources.schemas import (
    SourceArticlePage,
    SourceClusterPage,
    SourceCoverage,
    SourceDetail,
    SourceTiming,
)

router = APIRouter(tags=["sources"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Days = Annotated[int, Query(ge=1, le=366, description="Window length in UTC days, ending today.")]


async def _source_or_404(db: AsyncSession, source_id: uuid.UUID) -> Feed:
    """A source is a feed; retired feeds stay viewable because their evidence remains."""
    feed = await queries.get_source(db, source_id)
    if feed is None:
        raise HTTPException(404, "Source not found")
    return feed


def _cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


@router.get("/sources/{source_id}", response_model=SourceDetail)
async def get_source(source_id: uuid.UUID, db: Db, _auth: Auth, days: Days = 30) -> SourceDetail:
    return await queries.detail(db, await _source_or_404(db, source_id), days)


@router.get("/sources/{source_id}/coverage", response_model=SourceCoverage)
async def get_source_coverage(
    source_id: uuid.UUID, db: Db, _auth: Auth, days: Days = 30
) -> SourceCoverage:
    await _source_or_404(db, source_id)
    return await queries.coverage(db, source_id, days)


@router.get("/sources/{source_id}/timing", response_model=SourceTiming)
async def get_source_timing(
    source_id: uuid.UUID, db: Db, _auth: Auth, days: Days = 30
) -> SourceTiming:
    await _source_or_404(db, source_id)
    return await queries.timing(db, source_id, days)


@router.get("/sources/{source_id}/articles", response_model=SourceArticlePage)
async def get_source_articles(
    source_id: uuid.UUID, db: Db, _auth: Auth, cursor: str | None = None, limit: Limit = 30
) -> SourceArticlePage:
    await _source_or_404(db, source_id)
    return await queries.articles(db, source_id, limit, _cursor_or_400(cursor))


@router.get("/sources/{source_id}/clusters", response_model=SourceClusterPage)
async def get_source_clusters(
    source_id: uuid.UUID, db: Db, _auth: Auth, cursor: str | None = None, limit: Limit = 30
) -> SourceClusterPage:
    await _source_or_404(db, source_id)
    return await queries.clusters(db, source_id, limit, _cursor_or_400(cursor))


@router.get("/sources/{source_id}/fetches", response_model=FetchPage)
async def get_source_fetches(
    source_id: uuid.UUID, db: Db, _auth: Auth, cursor: str | None = None, limit: Limit = 30
) -> FetchPage:
    await _source_or_404(db, source_id)
    return await queries.fetches(db, source_id, limit, _cursor_or_400(cursor))
