import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, distinct, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.engine import CLUSTER_ENTITY_TYPES
from app.clustering.models import StoryCluster, StoryClusterMember
from app.events.models import STATUSES, Event, EventCluster, EventEntity
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity


async def create_event(db: AsyncSession, algorithm_version: str) -> Event:
    event = Event(algorithm_version=algorithm_version)
    db.add(event)
    await db.flush()
    await db.refresh(event)  # server defaults (status, timestamps)
    return event


async def get_event(db: AsyncSession, event_id: uuid.UUID) -> Event | None:
    return await db.get(Event, event_id)


async def event_for_cluster(
    db: AsyncSession, cluster_id: uuid.UUID, algorithm_version: str
) -> Event | None:
    result = await db.execute(
        select(Event)
        .join(EventCluster, EventCluster.event_id == Event.id)
        .where(
            EventCluster.cluster_id == cluster_id,
            EventCluster.algorithm_version == algorithm_version,
        )
    )
    return result.scalar_one_or_none()


async def associate_cluster(
    db: AsyncSession,
    event: Event,
    cluster_id: uuid.UUID,
    score: float,
    signals: dict[str, Any] | None = None,
    cluster_updated_at: datetime | None = None,
) -> EventCluster:
    """Idempotent: a cluster already in this version's event set is updated, or moved to `event`."""
    row = await db.scalar(
        select(EventCluster).where(
            EventCluster.algorithm_version == event.algorithm_version,
            EventCluster.cluster_id == cluster_id,
        )
    )
    if row is None:
        row = EventCluster(
            event_id=event.id, cluster_id=cluster_id, algorithm_version=event.algorithm_version
        )
        db.add(row)
    row.event_id, row.score, row.signals = event.id, score, signals or {}
    if cluster_updated_at is not None:
        row.cluster_updated_at = cluster_updated_at
    await db.flush()
    return row


async def set_entities(db: AsyncSession, event: Event, counts: dict[uuid.UUID, int]) -> None:
    """Make the event's entity set equal `counts` (entity id -> distinct article count).

    Only differences are written, so recomputing an unchanged event touches nothing.
    """
    existing = {
        row.entity_id: row
        for row in await db.scalars(select(EventEntity).where(EventEntity.event_id == event.id))
    }
    for entity_id, row in existing.items():
        if entity_id not in counts:
            await db.delete(row)
        else:
            row.article_count = counts[entity_id]
    db.add_all(
        EventEntity(event_id=event.id, entity_id=entity_id, article_count=count)
        for entity_id, count in counts.items()
        if entity_id not in existing
    )
    await db.flush()


async def refresh_span(db: AsyncSession, event: Event) -> None:
    """Recompute the cached time span from the member clusters' publication bounds."""
    first, last = (
        await db.execute(
            select(
                func.min(StoryCluster.first_published_at), func.max(StoryCluster.last_published_at)
            )
            .join(EventCluster, EventCluster.cluster_id == StoryCluster.id)
            .where(EventCluster.event_id == event.id)
        )
    ).one()
    event.started_at, event.ended_at = first, last
    await db.flush()


async def set_status(db: AsyncSession, event: Event, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown event status: {status}")
    event.status = status
    await db.flush()


def _event_articles(event_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """The article-to-event join every derived value is aggregated through."""
    return and_(
        StoryClusterMember.cluster_id == EventCluster.cluster_id, EventCluster.event_id == event_id
    )


async def refresh_event(db: AsyncSession, event: Event) -> bool:
    """Recompute the cached span, entity set and story country from the member clusters.

    An event left with no cluster is deleted (it is derived data); returns whether it survives.
    """
    if not await db.scalar(select(exists().where(EventCluster.event_id == event.id))):
        await db.delete(event)
        await db.flush()
        return False
    await refresh_span(db, event)
    entity_rows = await db.execute(
        select(ArticleEntity.entity_id, func.count(distinct(ArticleEntity.article_id)))
        .join(StoryClusterMember, StoryClusterMember.article_id == ArticleEntity.article_id)
        .join(EventCluster, _event_articles(event.id))
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(ArticleEntity.is_current.is_(True), Entity.entity_type.in_(CLUSTER_ENTITY_TYPES))
        .group_by(ArticleEntity.entity_id)
    )
    await set_entities(db, event, {entity: count for entity, count in entity_rows})
    # Story-role countries only; the most frequent wins, ties go to the alphabetically first code.
    country = await db.scalar(
        select(ArticleCountryAnnotation.country_code)
        .join(
            StoryClusterMember,
            StoryClusterMember.article_id == ArticleCountryAnnotation.article_id,
        )
        .join(EventCluster, _event_articles(event.id))
        .where(
            ArticleCountryAnnotation.is_current.is_(True),
            ArticleCountryAnnotation.role == "primary",
        )
        .group_by(ArticleCountryAnnotation.country_code)
        .order_by(
            func.count(distinct(ArticleCountryAnnotation.article_id)).desc(),
            ArticleCountryAnnotation.country_code,
        )
        .limit(1)
    )
    event.primary_country = country
    await db.flush()
    return True
