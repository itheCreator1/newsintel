"""Whether a name is in use: an article names it, directly or as the variant it was seen as, or
another name points at it. A name in use is merged; one that is not can simply be adopted."""

import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models import Entity
from app.nlp.models import ArticleEntity


async def in_use(db: AsyncSession, entity_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    or_(
                        ArticleEntity.entity_id == entity_id,
                        ArticleEntity.observed_entity_id == entity_id,
                    )
                )
                | exists().where(Entity.authority_id == entity_id)
            )
        )
    )
