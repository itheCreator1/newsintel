"""Replaceable event association.

The default associator scores story clusters against recent events with PostgreSQL rules only:
time overlap, shared entities, headline overlap and story country. The choice for one cluster is
a pure function of the facts loaded here, so a fixed database state gives a fixed result.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy import and_, distinct, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.engine import CLUSTER_ENTITY_TYPES, jaccard, title_tokens
from app.clustering.models import StoryCluster, StoryClusterMember
from app.events.models import Event, EventCluster, EventEntity
from app.events.service import associate_cluster, create_event, refresh_event
from app.feeds.models import Article
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity
from app.nlp.service import current_stop_words

log = structlog.get_logger()

EVENT_ALGORITHM_VERSION = "rule-1"
EVENT_TIME_WINDOW = timedelta(hours=72)
EVENT_SCORE_THRESHOLD = 0.5
EVENT_MIN_SHARED_ENTITIES = 2
EVENT_CANDIDATE_LIMIT = 50
EVENT_BATCH = 100
EVENT_RECONCILE_WINDOW = timedelta(days=7)
EVENT_RECONCILE_LIMIT = 500
TIME_WEIGHT = 0.25
ENTITY_WEIGHT = 0.4
TITLE_WEIGHT = 0.2
LOCATION_WEIGHT = 0.15
# Decisions read the whole event set of a version, so each batch serialises behind one lock.
# ponytail: one global lock; shard by country or time bucket if runs ever queue behind it.
EVENT_ADVISORY_LOCK = 728341907


@dataclass(frozen=True)
class ClusterFacts:
    id: uuid.UUID
    first_published_at: datetime
    last_published_at: datetime
    title_tokens: frozenset[str]
    entity_ids: frozenset[uuid.UUID]
    country: str | None


@dataclass(frozen=True)
class EventFacts:
    id: uuid.UUID
    started_at: datetime | None
    ended_at: datetime | None
    entity_ids: frozenset[uuid.UUID]
    member_titles: tuple[frozenset[str], ...]
    country: str | None


@dataclass(frozen=True)
class Score:
    total: float
    signals: dict[str, float]


@dataclass
class BatchResult:
    evaluated: int = 0
    created: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: bool = False


def time_gap(cluster: ClusterFacts, event: EventFacts) -> timedelta | None:
    """Zero when the spans overlap; `None` when the event has no span yet."""
    if event.started_at is None or event.ended_at is None:
        return None
    gap = max(cluster.first_published_at, event.started_at) - min(
        cluster.last_published_at, event.ended_at
    )
    return max(gap, timedelta(0))


def score_cluster(cluster: ClusterFacts, event: EventFacts) -> Score:
    gap = time_gap(cluster, event)
    signals = {
        "time": 0.0 if gap is None else max(0.0, 1.0 - gap / EVENT_TIME_WINDOW),
        "entities": jaccard(cluster.entity_ids, event.entity_ids),
        # The closest member headline, so a growing event does not dilute its own match.
        "title": max((jaccard(cluster.title_tokens, t) for t in event.member_titles), default=0.0),
        # Unknown or different never rewards; unknown is not a penalty either.
        "location": float(cluster.country is not None and cluster.country == event.country),
    }
    total = (
        TIME_WEIGHT * signals["time"]
        + ENTITY_WEIGHT * signals["entities"]
        + TITLE_WEIGHT * signals["title"]
        + LOCATION_WEIGHT * signals["location"]
    )
    return Score(total, signals)


def choose_event(
    cluster: ClusterFacts, candidates: Sequence[EventFacts], *, current: EventFacts | None = None
) -> tuple[EventFacts, Score] | None:
    """Stay in the current event while it still qualifies, else the best candidate, else none.

    Candidates rank by score, then the smaller time gap, then the smaller event id.
    """
    if current is not None:
        score = score_cluster(cluster, current)
        if score.total >= EVENT_SCORE_THRESHOLD:
            return current, score
    ranked = []
    for event in candidates:
        if current is not None and event.id == current.id:
            continue
        score = score_cluster(cluster, event)
        if score.total >= EVENT_SCORE_THRESHOLD:
            ranked.append(
                (-score.total, time_gap(cluster, event) or timedelta(0), event.id, event, score)
            )
    if not ranked:
        return None
    _, _, _, event, score = min(ranked, key=lambda item: item[:3])
    return event, score


async def load_cluster_facts(
    db: AsyncSession, clusters: Sequence[StoryCluster], stop_words: frozenset[str]
) -> dict[uuid.UUID, ClusterFacts]:
    ids = [c.id for c in clusters]
    entities: dict[uuid.UUID, set[uuid.UUID]] = {}
    for cluster_id, entity_id in await db.execute(
        select(StoryClusterMember.cluster_id, ArticleEntity.entity_id)
        .join(ArticleEntity, ArticleEntity.article_id == StoryClusterMember.article_id)
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(
            StoryClusterMember.cluster_id.in_(ids),
            ArticleEntity.is_current.is_(True),
            Entity.entity_type.in_(CLUSTER_ENTITY_TYPES),
        )
        .distinct()
    ):
        entities.setdefault(cluster_id, set()).add(entity_id)
    countries: dict[uuid.UUID, list[tuple[int, str]]] = {}
    for cluster_id, code, count in await db.execute(
        select(
            StoryClusterMember.cluster_id,
            ArticleCountryAnnotation.country_code,
            func.count(distinct(ArticleCountryAnnotation.article_id)),
        )
        .join(
            ArticleCountryAnnotation,
            ArticleCountryAnnotation.article_id == StoryClusterMember.article_id,
        )
        .where(
            StoryClusterMember.cluster_id.in_(ids),
            ArticleCountryAnnotation.is_current.is_(True),
            ArticleCountryAnnotation.role == "primary",
        )
        .group_by(StoryClusterMember.cluster_id, ArticleCountryAnnotation.country_code)
    ):
        countries.setdefault(cluster_id, []).append((-count, code))
    titles = dict(
        (
            await db.execute(
                select(Article.id, Article.title).where(
                    Article.id.in_(
                        [
                            c.representative_article_id
                            for c in clusters
                            if c.representative_article_id
                        ]
                    )
                )
            )
        )
        .tuples()
        .all()
    )
    return {
        c.id: ClusterFacts(
            id=c.id,
            first_published_at=c.first_published_at,  # type: ignore[arg-type]
            last_published_at=c.last_published_at,  # type: ignore[arg-type]
            title_tokens=title_tokens(
                titles.get(c.representative_article_id, "") if c.representative_article_id else "",
                stop_words,
            ),
            entity_ids=frozenset(entities.get(c.id, ())),
            country=min(countries[c.id])[1] if c.id in countries else None,
        )
        for c in clusters
    }


async def load_event_facts(
    db: AsyncSession, event_ids: Sequence[uuid.UUID], stop_words: frozenset[str]
) -> dict[uuid.UUID, EventFacts]:
    if not event_ids:
        return {}
    entities: dict[uuid.UUID, set[uuid.UUID]] = {}
    for event_id, entity_id in await db.execute(
        select(EventEntity.event_id, EventEntity.entity_id).where(
            EventEntity.event_id.in_(event_ids)
        )
    ):
        entities.setdefault(event_id, set()).add(entity_id)
    titles: dict[uuid.UUID, list[frozenset[str]]] = {}
    for event_id, title in await db.execute(
        select(EventCluster.event_id, Article.title)
        .join(StoryCluster, StoryCluster.id == EventCluster.cluster_id)
        .join(Article, Article.id == StoryCluster.representative_article_id)
        .where(EventCluster.event_id.in_(event_ids))
        .order_by(EventCluster.cluster_id)
    ):
        titles.setdefault(event_id, []).append(title_tokens(title, stop_words))
    rows = await db.execute(
        select(Event.id, Event.started_at, Event.ended_at, Event.primary_country).where(
            Event.id.in_(event_ids)
        )
    )
    return {
        event_id: EventFacts(
            event_id,
            started_at,
            ended_at,
            frozenset(entities.get(event_id, ())),
            tuple(titles.get(event_id, ())),
            country,
        )
        for event_id, started_at, ended_at, country in rows
    }


async def candidate_event_ids(
    db: AsyncSession, cluster: ClusterFacts, version: str
) -> list[uuid.UUID]:
    """Recent active events sharing entities with the cluster, newest first; never all pairs."""
    if not cluster.entity_ids:
        return []
    query = (
        select(Event.id)
        .join(EventEntity, EventEntity.event_id == Event.id)
        .where(
            Event.algorithm_version == version,
            Event.status == "active",
            Event.ended_at >= cluster.first_published_at - EVENT_TIME_WINDOW,
            Event.started_at <= cluster.last_published_at + EVENT_TIME_WINDOW,
            EventEntity.entity_id.in_(cluster.entity_ids),
        )
        .group_by(Event.id, Event.ended_at)
        .having(func.count() >= EVENT_MIN_SHARED_ENTITIES)
        .order_by(Event.ended_at.desc(), Event.id)
        .limit(EVENT_CANDIDATE_LIMIT)
    )
    return list((await db.scalars(query)).all())


async def _lock(db: AsyncSession) -> bool:
    return bool(await db.scalar(text(f"SELECT pg_try_advisory_xact_lock({EVENT_ADVISORY_LOCK})")))


@runtime_checkable
class EventAssociator(Protocol):
    version: str

    async def run_batch(self, db: AsyncSession, limit: int) -> BatchResult: ...


class RuleEventAssociator:
    """Associates each changed story cluster with one event per algorithm version."""

    version = EVENT_ALGORITHM_VERSION

    async def run_batch(self, db: AsyncSession, limit: int) -> BatchResult:
        """Decide up to `limit` clusters that are new or changed since their last decision.

        The caller owns the transaction. A cluster whose decision fails is logged, rolled back on
        its own and stays changed, so the next run retries it without blocking the others.
        """
        result = BatchResult()
        if not await _lock(db):
            result.skipped = True
            return result
        rows = (
            await db.execute(
                select(StoryCluster, EventCluster.event_id)
                .outerjoin(
                    EventCluster,
                    and_(
                        EventCluster.cluster_id == StoryCluster.id,
                        EventCluster.algorithm_version == self.version,
                    ),
                )
                .where(
                    StoryCluster.article_count >= 2,
                    StoryCluster.first_published_at.is_not(None),
                    StoryCluster.last_published_at.is_not(None),
                    or_(
                        EventCluster.cluster_id.is_(None),
                        StoryCluster.updated_at > EventCluster.cluster_updated_at,
                    ),
                )
                .order_by(StoryCluster.first_published_at, StoryCluster.id)
                .limit(limit)
                # Blocks a concurrent dissolve until commit; a plain update (growth) does not wait.
                .with_for_update(key_share=True, skip_locked=True, of=StoryCluster)
            )
        ).all()
        if not rows:
            return result
        stop_words = frozenset((await current_stop_words(db)).words)
        facts = await load_cluster_facts(db, [c for c, _ in rows], stop_words)
        for cluster, current_id in rows:
            try:
                async with db.begin_nested():
                    created, deleted = await self._decide(
                        db, facts[cluster.id], cluster.updated_at, current_id, stop_words
                    )
                result.created += created
                result.deleted += deleted
                result.evaluated += 1
            except Exception:
                result.failed += 1
                log.exception(
                    "event_association_failed",
                    error_category="event_association",
                    cluster_id=str(cluster.id),
                )
        return result

    async def _decide(
        self,
        db: AsyncSession,
        cluster: ClusterFacts,
        cluster_updated_at: datetime,
        current_id: uuid.UUID | None,
        stop_words: frozenset[str],
    ) -> tuple[int, int]:
        candidate_ids = await candidate_event_ids(db, cluster, self.version)
        loaded = await load_event_facts(
            db, [*candidate_ids, *([current_id] if current_id else [])], stop_words
        )
        chosen = choose_event(
            cluster,
            [loaded[i] for i in candidate_ids if i in loaded],
            current=loaded.get(current_id) if current_id else None,
        )
        created = 0
        if chosen is None:
            event = await create_event(db, self.version)
            created = 1
            score, signals = 0.0, {}
        else:
            event = await db.get(Event, chosen[0].id)  # type: ignore[assignment]
            score, signals = chosen[1].total, chosen[1].signals
        await associate_cluster(
            db, event, cluster.id, score, signals, cluster_updated_at=cluster_updated_at
        )
        deleted = 0
        for event_id in {event.id, current_id} - {None}:
            target = await db.get(Event, event_id)
            if target is not None and not await refresh_event(db, target):
                deleted += 1
        return created, deleted


async def reconcile_events(db: AsyncSession, version: str) -> BatchResult:
    """Refresh recent events, which loses members when clusters merge or dissolve.

    ponytail: bounded to the newest `EVENT_RECONCILE_LIMIT` events of the last week; an event that
    loses a member later, or beyond that limit, stays stale until its next cluster decision.
    """
    result = BatchResult()
    if not await _lock(db):
        result.skipped = True
        return result
    events = await db.scalars(
        select(Event)
        .where(
            Event.algorithm_version == version,
            Event.status == "active",
            or_(
                Event.ended_at.is_(None),
                Event.ended_at >= datetime.now(UTC) - EVENT_RECONCILE_WINDOW,
            ),
        )
        .order_by(Event.ended_at.desc().nulls_first(), Event.id)
        .limit(EVENT_RECONCILE_LIMIT)
    )
    for event in list(events):
        result.evaluated += 1
        if not await refresh_event(db, event):
            result.deleted += 1
    return result


def summary(result: BatchResult) -> dict[str, Any]:
    return {
        "evaluated": result.evaluated,
        "created": result.created,
        "deleted": result.deleted,
        "failed": result.failed,
    }
