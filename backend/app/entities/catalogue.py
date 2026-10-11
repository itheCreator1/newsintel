"""The authority file as a whole: its roots by name and every change, newest first."""

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, exists, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.feeds.service import encode_cursor
from app.nlp.models import ArticleEntity, Entity, EntityAuthorityChange
from app.wikidata.models import EntityExternalId

__all__ = ["RootCursor", "decode_root_cursor", "recent_changes", "roots"]

# Where a page of roots ends: (article count, lowercased name, id), the count None when the
# page is in name order.
RootCursor = tuple[int | None, str, uuid.UUID]


def _encode_root_cursor(count: int | None, name: str, item_id: uuid.UUID) -> str:
    values: list[Any] = [name, str(item_id)] if count is None else [count, name, str(item_id)]
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode()


def decode_root_cursor(cursor: str, *, by_articles: bool) -> RootCursor:
    """The place a page of roots in this order ends at; ValueError when malformed."""
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(values, list) or len(values) != (3 if by_articles else 2):
            raise ValueError("Invalid cursor")
        count = values.pop(0) if by_articles else None
        name, item_id = values
        if not isinstance(name, str) or (by_articles and type(count) is not int):
            raise ValueError("Invalid cursor")
        return count, name, uuid.UUID(item_id)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc


async def roots(
    db: AsyncSession,
    *,
    language: str | None,
    status: str | None,
    q: str | None,
    limit: int,
    cursor: RootCursor | None,
    wikidata: str | None = None,
    hidden: bool = False,
    types: list[str] | None = None,
    by_articles: bool = False,
) -> tuple[list[tuple[Entity, int, str | None, int]], str | None]:
    """Roots with their variant counts, QIDs and article counts; `q` finds a root by any of its
    names. In name order, or with the most articles first (`by_articles`), then by name.

    `wikidata`: "linked" or "unlinked" keeps the roots with or without a QID. `hidden` lists the
    roots hidden as not a name instead of the others. `types` keeps the roots shown as one of
    these types.
    """
    variant = aliased(Entity)
    sort_name = func.lower(Entity.name)
    variant_count = (
        select(func.count())
        .where(variant.authority_id == Entity.id)
        .correlate(Entity)
        .scalar_subquery()
    )
    qid = (
        select(EntityExternalId.value)
        .where(EntityExternalId.entity_id == Entity.id, EntityExternalId.scheme == "wikidata")
        .correlate(Entity)
        .scalar_subquery()
    )
    # The partial index on (entity_id, article_id) WHERE is_current serves this per root.
    article_count = (
        select(func.count(func.distinct(ArticleEntity.article_id)))
        .where(ArticleEntity.entity_id == Entity.id, ArticleEntity.is_current.is_(True))
        .correlate(Entity)
        .scalar_subquery()
    )
    query = select(Entity, variant_count, sort_name, qid, article_count).where(
        Entity.authority_id.is_(None), Entity.hidden.is_(hidden)
    )
    if types:
        query = query.where(Entity.kind.in_(types))
    if wikidata == "linked":
        query = query.where(qid.is_not(None))
    elif wikidata == "unlinked":
        query = query.where(qid.is_(None))
    if language is not None:
        query = query.where(Entity.language == language)
    if status is not None:
        query = query.where(Entity.status == status)
    if q:
        escaped = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                # Postgres lowercases the name, so it lowercases the pattern too (Σ, ς).
                sort_name.like(func.lower(pattern)),
                Entity.normalized_text.like(pattern),
                exists().where(
                    variant.authority_id == Entity.id, variant.normalized_text.like(pattern)
                ),
            )
        )
    # Most articles first is ascending on the negated count, so one row comparison pages both.
    keys: list[ColumnElement[Any]] = [sort_name, Entity.id.expression]
    if by_articles:
        keys.insert(0, -article_count)
    if cursor is not None:
        count, name, item_id = cursor
        after: list[ColumnElement[Any]] = [literal(name), literal(item_id)]
        if by_articles:
            after.insert(0, literal(-(count or 0)))
        query = query.where(tuple_(*keys) > tuple_(*after))
    rows = list(await db.execute(query.order_by(*keys).limit(limit + 1)))
    page = [(row[0], row[1], row[3], int(row[4])) for row in rows[:limit]]
    # The database's own lowercase, so the next page starts exactly where this one ended.
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode_root_cursor(
            int(last[4]) if by_articles else None, last[2], last[0].id
        )
    return page, next_cursor


async def recent_changes(
    db: AsyncSession, *, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> tuple[list[tuple[EntityAuthorityChange, str | None, str | None]], str | None]:
    """Every change to the file, newest first, with the current names of both entities in it."""
    other = aliased(Entity)
    query: Any = (
        select(EntityAuthorityChange, Entity.name, other.name)
        .outerjoin(Entity, Entity.id == EntityAuthorityChange.entity_id)
        .outerjoin(other, other.id == EntityAuthorityChange.other_id)
    )
    if cursor is not None:
        query = query.where(
            tuple_(EntityAuthorityChange.created_at, EntityAuthorityChange.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    rows = list(
        await db.execute(
            query.order_by(
                EntityAuthorityChange.created_at.desc(), EntityAuthorityChange.id.desc()
            ).limit(limit + 1)
        )
    )
    page = [(row[0], row[1], row[2]) for row in rows[:limit]]
    next_cursor = (
        encode_cursor(page[-1][0].created_at, page[-1][0].id) if len(rows) > limit else None
    )
    return page, next_cursor
