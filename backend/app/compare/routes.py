import re
import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursors import cursor_or_400
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
from app.entities.resolver import resolve_root

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


async def _by_root(db: AsyncSession, compared: Spec) -> Spec:
    """Entities are compared by root, so a merged id stands for the entity it was merged into."""
    if compared.kind != "entity":
        return compared
    a = await resolve_root(db, uuid.UUID(compared.a)) or compared.a
    b = await resolve_root(db, uuid.UUID(compared.b)) or compared.b
    if str(a) == str(b):
        raise HTTPException(422, "Choose two different subjects")
    return replace(compared, a=str(a), b=str(b))


Compared = Annotated[Spec, Depends(spec)]


async def _subjects(db: AsyncSession, compared: Spec) -> tuple[Spec, Subject, Subject]:
    compared = await _by_root(db, compared)
    first = await queries.resolve(db, compared, compared.a)
    second = await queries.resolve(db, compared, compared.b)
    if first is None or second is None:
        raise HTTPException(404, "Subject not found")
    return compared, first, second


@router.get("/compare", response_model=CompareResponse)
async def compare(db: Db, _auth: Auth, compared: Compared) -> CompareResponse:
    compared, first, second = await _subjects(db, compared)
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
    cursor_value = cursor_or_400(cursor)
    compared, _, _ = await _subjects(db, compared)
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
    cursor_value = cursor_or_400(cursor)
    compared, _, _ = await _subjects(db, compared)
    return await queries.clusters(db, compared, part, limit, cursor_value)
