"""Which root an entity id stands for, and every id that stands for the same root."""

import uuid
from collections.abc import Iterable

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.nlp.models import Entity


def names_of(entity_ids: list[uuid.UUID]) -> Select[tuple[uuid.UUID]]:
    """The ids of every name of the roots these ids stand for, as a subquery to filter with."""
    roots = select(func.coalesce(Entity.authority_id, Entity.id)).where(Entity.id.in_(entity_ids))
    return select(Entity.id).where(or_(Entity.id.in_(roots), Entity.authority_id.in_(roots)))


async def resolve_root(db: AsyncSession, entity_id: uuid.UUID) -> uuid.UUID | None:
    """The root an id stands for (itself when it is one), or None for an unknown id."""
    row = (
        await db.execute(select(Entity.id, Entity.authority_id).where(Entity.id == entity_id))
    ).first()
    return None if row is None else row.authority_id or row.id


async def expand_for_search(db: AsyncSession, ids: Iterable[str]) -> list[str]:
    """Every id of the roots these ids stand for: the root and all its variants.

    Articles keep their indexed entity ids until a merge run reaches them, so a filter on the
    root alone would lose them in between; old ids in saved searches and monitors keep working.
    An id the archive does not know stays as it is and matches nothing.
    """
    requested = sorted(set(ids))
    if not requested:
        return []
    found = await db.scalars(names_of([uuid.UUID(value) for value in requested]))
    return sorted({*requested, *(str(value) for value in found)})


async def entity_group(
    db: AsyncSession, entity_id: uuid.UUID
) -> tuple[uuid.UUID, list[str]] | None:
    """The root of an id and every id to match it by, or None for an unknown id."""
    root_id = await resolve_root(db, entity_id)
    if root_id is None:
        return None
    return root_id, await expand_for_search(db, [str(root_id)])
