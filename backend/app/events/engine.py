"""Replaceable event association.

The default associator scores story clusters against recent events with PostgreSQL rules only:
time overlap, shared entities, headline overlap and story country. The choice for one cluster is
a pure function of the facts loaded here, so a fixed database state gives a fixed result.
"""

import math
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy import Select, and_, distinct, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.engine import CLUSTER_ENTITY_TYPES, jaccard, title_tokens
from app.clustering.models import StoryCluster, StoryClusterMember
from app.events.models import Event, EventCluster, EventEntity
from app.events.service import associate_cluster, create_event, refresh_event
from app.feeds.models import Article
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity
from app.nlp.service import current_stop_words

log = structlog.get_logger()

# rule-2: entities match the closest member cluster, weighted so widely reported ones count less.
EVENT_ALGORITHM_VERSION = "rule-2"
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
    member_entities: tuple[frozenset[uuid.UUID], ...]
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
    last_error: str | None = None


def time_gap(cluster: ClusterFacts, event: EventFacts) -> timedelta | None:
    """Zero when the spans overlap; `None` when the event has no span yet."""
    if event.started_at is None or event.ended_at is None:
        return None
    gap = max(cluster.first_published_at, event.started_at) - min(
        cluster.last_published_at, event.ended_at
    )
    return max(gap, timedelta(0))


def entity_weight(article_count: int) -> float:
    """Below 1 and falling with how many articles mention the entity, so "US" counts less."""
    return 1.0 / math.log(math.e + article_count)


def weighted_jaccard[T](
    left: frozenset[T], right: frozenset[T], weights: Mapping[T, float]
) -> float:
    """Jaccard with each item counted at its weight; an item without one weighs 1."""
    if not left or not right:
        return 0.0
    shared = sum(weights.get(item, 1.0) for item in left & right)
    return shared / sum(weights.get(item, 1.0) for item in left | right)


def score_cluster(
    cluster: ClusterFacts, event: EventFacts, weights: Mapping[uuid.UUID, float] | None = None
) -> Score:
    gap = time_gap(cluster, event)
    weights = weights or {}
    signals = {
        "time": 0.0 if gap is None else max(0.0, 1.0 - gap / EVENT_TIME_WINDOW),
        # Entities and headline both match the closest member cluster, so a growing event does
        # not dilute its own match.
        "entities": max(
            (weighted_jaccard(cluster.entity_ids, e, weights) for e in event.member_entities),
            default=0.0,
        ),
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
    cluster: ClusterFacts,
    candidates: Sequence[EventFacts],
    *,
    current: EventFacts | None = None,
    weights: Mapping[uuid.UUID, float] | None = None,
) -> tuple[EventFacts, Score] | None:
    """Stay in the current event while it still qualifies, else the best candidate, else none.

    Candidates rank by score, then the smaller time gap, then the smaller event id.
    """
    if current is not None:
        score = score_cluster(cluster, current, weights)
        if score.total >= EVENT_SCORE_THRESHOLD:
            return current, score
    ranked = []
    for event in candidates:
        if current is not None and event.id == current.id:
            continue
        score = score_cluster(cluster, event, weights)
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
    """Each event with its member clusters' facts; span and country are the event's cached ones."""
    if not event_ids:
        return {}
    members = (
        await db.execute(
            select(EventCluster.event_id, StoryCluster)
            .join(StoryCluster, StoryCluster.id == EventCluster.cluster_id)
            .where(EventCluster.event_id.in_(event_ids))
            .order_by(EventCluster.cluster_id)
        )
    ).all()
    facts = await load_cluster_facts(db, [c for _, c in members], stop_words) if members else {}
    by_event: dict[uuid.UUID, list[ClusterFacts]] = {}
    for event_id, member in members:
        by_event.setdefault(event_id, []).append(facts[member.id])
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
            tuple(f.entity_ids for f in by_event.get(event_id, ())),
            tuple(f.title_tokens for f in by_event.get(event_id, ())),
            country,
        )
        for event_id, started_at, ended_at, country in rows
    }


async def load_entity_weights(
    db: AsyncSession, entity_ids: Iterable[uuid.UUID], known: dict[uuid.UUID, float]
) -> dict[uuid.UUID, float]:
    """Fill `known` with `entity_weight` for the entities it lacks, counted over current articles.

    ponytail: counts the whole archive; window it if counting a very common entity gets slow.
    """
    missing = set(entity_ids) - known.keys()
    if missing:
        counts = dict(
            (
                await db.execute(
                    select(ArticleEntity.entity_id, func.count(distinct(ArticleEntity.article_id)))
                    .where(ArticleEntity.entity_id.in_(missing), ArticleEntity.is_current.is_(True))
                    .group_by(ArticleEntity.entity_id)
                )
            )
            .tuples()
            .all()
        )
        known.update({i: entity_weight(counts.get(i, 0)) for i in missing})
    return known


async def event_facts_without(
    db: AsyncSession, event_id: uuid.UUID, cluster_id: uuid.UUID, stop_words: frozenset[str]
) -> EventFacts | None:
    """The event as its other members describe it, so a cluster never matches its own evidence.

    None when the cluster is the event's only member.
    ponytail: the country is a vote of the members' countries; `refresh_event` weights articles.
    """
    others = list(
        await db.scalars(
            select(StoryCluster)
            .join(EventCluster, EventCluster.cluster_id == StoryCluster.id)
            .where(EventCluster.event_id == event_id, StoryCluster.id != cluster_id)
            .order_by(StoryCluster.id)
        )
    )
    if not others:
        return None
    facts = list((await load_cluster_facts(db, others, stop_words)).values())
    countries = Counter(f.country for f in facts if f.country)
    return EventFacts(
        event_id,
        min(f.first_published_at for f in facts),
        max(f.last_published_at for f in facts),
        tuple(f.entity_ids for f in facts),
        tuple(f.title_tokens for f in facts),
        min(countries, key=lambda code: (-countries[code], code)) if countries else None,
    )


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


def dirty_clusters(version: str) -> Select[tuple[StoryCluster, uuid.UUID]]:
    """Clusters that are new or changed since their decision for `version`, with their event id.

    The one definition of "dirty": the batch reads it and the operations page counts it.
    """
    return (
        select(StoryCluster, EventCluster.event_id)
        .outerjoin(
            EventCluster,
            and_(
                EventCluster.cluster_id == StoryCluster.id,
                EventCluster.algorithm_version == version,
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
    )


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
                dirty_clusters(self.version)
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
        weights: dict[uuid.UUID, float] = {}  # per batch: counts move as articles arrive
        for cluster, current_id in rows:
            try:
                async with db.begin_nested():
                    created, deleted = await self._decide(
                        db, facts[cluster.id], cluster.updated_at, current_id, stop_words, weights
                    )
                result.created += created
                result.deleted += deleted
                result.evaluated += 1
            except Exception as exc:
                result.failed += 1
                result.last_error = f"{type(exc).__name__}: {exc}"[:1000]
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
        weights: dict[uuid.UUID, float],
    ) -> tuple[int, int]:
        candidate_ids = [
            i for i in await candidate_event_ids(db, cluster, self.version) if i != current_id
        ]
        loaded = await load_event_facts(db, candidate_ids, stop_words)
        current = (
            await event_facts_without(db, current_id, cluster.id, stop_words)
            if current_id
            else None
        )
        candidates = [loaded[i] for i in candidate_ids if i in loaded]
        # Only shared entities move a score up, but the union needs every weight.
        await load_entity_weights(
            db,
            cluster.entity_ids.union(
                *(e for event in [*candidates, current] if event for e in event.member_entities)
            ),
            weights,
        )
        chosen = choose_event(cluster, candidates, current=current, weights=weights)
        created = 0
        event: Event
        if chosen is None:
            if current_id and current is None:  # its own event: leaving would only recreate it
                event = await db.get(Event, current_id)  # type: ignore[assignment]
            else:
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
