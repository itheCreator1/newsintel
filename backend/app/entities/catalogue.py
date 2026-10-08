"""The authority file as a whole: its roots by name and every change, newest first."""

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.feeds.service import encode_cursor
from app.nlp.models import Entity, EntityAuthorityChange

__all__ = ["decode_name_cursor", "recent_changes", "roots"]


def _encode_name_cursor(name: str, item_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps([name, str(item_id)]).encode()).decode()


def decode_name_cursor(cursor: str) -> tuple[str, uuid.UUID]:
    """The (lowercased name, id) a page of roots ends at; ValueError when malformed."""
    try:
        name, item_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(name, str):
            raise ValueError("Invalid cursor")
        return name, uuid.UUID(item_id)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc


async def roots(
    db: AsyncSession,
    *,
    language: str | None,
    status: str | None,
    q: str | None,
    limit: int,
    cursor: tuple[str, uuid.UUID] | None,
) -> tuple[list[tuple[Entity, int]], str | None]:
    """Roots by name with their variant counts; `q` finds a root by any of its names."""
    variant = aliased(Entity)
    sort_name = func.lower(Entity.name)
    variant_count = (
        select(func.count())
        .where(variant.authority_id == Entity.id)
        .correlate(Entity)
        .scalar_subquery()
    )
    query = select(Entity, variant_count, sort_name).where(Entity.authority_id.is_(None))
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
    if cursor is not None:
        query = query.where(
            tuple_(sort_name, Entity.id) > tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    rows = list(await db.execute(query.order_by(sort_name, Entity.id).limit(limit + 1)))
    page = [(row[0], row[1]) for row in rows[:limit]]
    # The database's own lowercase, so the next page starts exactly where this one ended.
    next_cursor = (
        _encode_name_cursor(rows[limit - 1][2], rows[limit - 1][0].id)
        if len(rows) > limit
        else None
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
