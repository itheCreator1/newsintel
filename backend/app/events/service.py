import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryCluster
from app.events.models import STATUSES, Event, EventCluster, EventEntity


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
    await db.flush()
    return row


async def set_entities(db: AsyncSession, event: Event, counts: dict[uuid.UUID, int]) -> None:
    """Replace the event's entity set with `counts` (entity id -> distinct article count)."""
    await db.execute(delete(EventEntity).where(EventEntity.event_id == event.id))
    db.add_all(
        EventEntity(event_id=event.id, entity_id=entity_id, article_count=count)
        for entity_id, count in counts.items()
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
