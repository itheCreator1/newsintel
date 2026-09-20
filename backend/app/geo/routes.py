import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.feeds.service import decode_cursor
from app.geo import queries
from app.geo.schemas import ArticleRole, GeoArticlePage, GeoCountriesResponse, Role

router = APIRouter(tags=["geo"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Days = Annotated[int, Query(ge=1, le=366, description="Window length in UTC days, ending today.")]
COUNTRY = re.compile(r"[A-Za-z]{2}")


def _cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


@router.get("/geo/countries", response_model=GeoCountriesResponse)
async def geo_countries(db: Db, _auth: Auth, role: Role, days: Days = 30) -> GeoCountriesResponse:
    return await queries.countries(db, role, days)


@router.get("/geo/articles", response_model=GeoArticlePage)
async def geo_articles(
    db: Db,
    _auth: Auth,
    role: ArticleRole,
    code: Annotated[str, Query(min_length=2, max_length=2)],
    days: Days = 30,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> GeoArticlePage:
    if not COUNTRY.fullmatch(code):
        raise HTTPException(422, "A country is a two-letter code")
    cursor_value = _cursor_or_400(cursor)
    return await queries.articles(db, role, code.upper(), days, limit, cursor_value)
