import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.compare import queries
from app.compare.queries import Spec
from app.compare.schemas import (
    CompareArticlePage,
    CompareClusterPage,
    CompareResponse,
    Kind,
    Part,
    Role,
    Subject,
)
from app.db.session import get_db
from app.feeds.service import decode_cursor

router = APIRouter(tags=["compare"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Ref = Annotated[str, Query(min_length=1, max_length=64)]
Days = Annotated[int, Query(ge=1, le=366, description="Window length in UTC days, ending today.")]
COUNTRY = re.compile(r"[A-Za-z]{2}")


def _ref(kind: Kind, value: str) -> str:
    """An entity or source id, or a country code in upper case; anything else is a 422."""
    if kind == "country":
        if not COUNTRY.fullmatch(value):
            raise HTTPException(422, "A country is a two-letter code")
        return value.upper()
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(422, f"A {kind} is identified by its id") from None


def spec(
    kind: Kind,
    a: Ref,
    b: Ref,
    role: Annotated[
        Role | None,
        Query(description="Required for countries (which country meaning), rejected otherwise."),
    ] = None,
    days: Days = 30,
) -> Spec:
    if (kind == "country") != (role is not None):
        raise HTTPException(422, "role is required for countries and only for countries")
    first, second = _ref(kind, a), _ref(kind, b)
    if first == second:
        raise HTTPException(422, "Choose two different subjects")
    return Spec(kind=kind, a=first, b=second, role=role, days=days)


Compared = Annotated[Spec, Depends(spec)]


def _cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


async def _subjects(db: AsyncSession, compared: Spec) -> tuple[Subject, Subject]:
    first = await queries.resolve(db, compared, compared.a)
    second = await queries.resolve(db, compared, compared.b)
    if first is None or second is None:
        raise HTTPException(404, "Subject not found")
    return first, second


@router.get("/compare", response_model=CompareResponse)
async def compare(db: Db, _auth: Auth, compared: Compared) -> CompareResponse:
    first, second = await _subjects(db, compared)
    return await queries.summary(db, compared, first, second)


@router.get("/compare/articles", response_model=CompareArticlePage)
async def compare_articles(
    db: Db,
    _auth: Auth,
    compared: Compared,
    part: Part,
    cursor: str | None = None,
    limit: Limit = 30,
) -> CompareArticlePage:
    cursor_value = _cursor_or_400(cursor)
    await _subjects(db, compared)
    return await queries.articles(db, compared, part, limit, cursor_value)


@router.get("/compare/stories", response_model=CompareClusterPage)
async def compare_stories(
    db: Db,
    _auth: Auth,
    compared: Compared,
    part: Part,
    cursor: str | None = None,
    limit: Limit = 30,
) -> CompareClusterPage:
    cursor_value = _cursor_or_400(cursor)
    await _subjects(db, compared)
    return await queries.clusters(db, compared, part, limit, cursor_value)
