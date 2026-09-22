import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.feeds.service import decode_cursor
from app.geo import investigation, queries
from app.geo.schemas import ArticleRole, GeoArticlePage, GeoCountriesResponse, MapScope, Role
from app.search.criteria import SearchCriteria, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter

router = APIRouter(tags=["geo"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Criteria = Annotated[SearchCriteria, Depends(search_criteria)]
Days = Annotated[
    int | None,
    Query(ge=1, le=366, description="Recent maps only: window length in UTC days, ending today."),
]
COUNTRY = re.compile(r"[A-Za-z]{2}")
DEFAULT_DAYS = 30


def _cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


def recent_days(scope: MapScope, criteria: SearchCriteria, days: int | None) -> int | None:
    """A recent map's window, or None for an investigation; the two modes never mix."""
    if scope == "recent":
        if not criteria.empty:
            raise HTTPException(
                422,
                {
                    "code": "investigation_scope_required",
                    "message": "Article criteria need scope=investigation; a recent map takes days",
                },
            )
        return days or DEFAULT_DAYS
    if days is not None:
        raise HTTPException(
            422,
            {
                "code": "days_not_in_investigation",
                "message": "An investigation map is bounded by after and before, not days",
            },
        )
    return None


@router.get("/geo/countries", response_model=GeoCountriesResponse)
async def geo_countries(
    db: Db,
    _auth: Auth,
    settings: Config,
    criteria: Criteria,
    role: Role,
    scope: MapScope = "recent",
    days: Days = None,
) -> GeoCountriesResponse:
    window = recent_days(scope, criteria, days)
    if window is not None:
        return await queries.countries(db, role, window)
    if role == "event":
        raise HTTPException(
            422,
            {
                "code": "event_scope_unsupported",
                "message": "Event maps take no article criteria; use scope=recent",
            },
        )
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    return await investigation.countries(db, adapter, role, criteria)


@router.get("/geo/articles", response_model=GeoArticlePage)
async def geo_articles(
    db: Db,
    _auth: Auth,
    role: ArticleRole,
    code: Annotated[str, Query(min_length=2, max_length=2)],
    days: Annotated[
        int, Query(ge=1, le=366, description="Window length in UTC days, ending today.")
    ] = 30,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> GeoArticlePage:
    if not COUNTRY.fullmatch(code):
        raise HTTPException(422, "A country is a two-letter code")
    cursor_value = _cursor_or_400(cursor)
    return await queries.articles(db, role, code.upper(), days, limit, cursor_value)
