"""Replaceable story clustering.

The default clusterer uses PostgreSQL rules only, so related reporting keeps
working while Elasticsearch is unavailable.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import ColumnElement, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryCluster, StoryClusterMember
from app.feeds.models import Article, FeedArticle
from app.nlp.models import ArticleEntity, Entity
from app.nlp.service import current_stop_words

CLUSTER_ALGORITHM_VERSION = "rule-1"
CLUSTER_TIME_WINDOW = timedelta(hours=48)
CLUSTER_CANDIDATE_LIMIT = 200
CLUSTER_SCORE_THRESHOLD = 0.45
CLUSTER_ENTITY_TYPES = ("ORG", "PERSON", "GPE", "EVENT")
CLUSTER_MINIMUM_SHARED_ENTITIES = 2
TITLE_WEIGHT = 0.5
ENTITY_WEIGHT = 0.35
TIME_WEIGHT = 0.15
# Clustering writes span articles, so every assignment serialises behind one lock.
CLUSTERING_ADVISORY_LOCK = 728341906

_TOKEN_PATTERN = re.compile(r"[0-9a-z]+")


@dataclass(frozen=True)
class ArticleFeatures:
    article_id: uuid.UUID
    title: str
    normalized_title_hash: str
    effective_date: datetime
    entity_ids: frozenset[uuid.UUID]
    feed_ids: frozenset[uuid.UUID]


@dataclass(frozen=True)
class ScoredCandidate:
    features: ArticleFeatures
    score: float


@dataclass(frozen=True)
class ClusterSummary:
    id: uuid.UUID
    created_at: datetime
    article_count: int


@dataclass(frozen=True)
class ClusterStatistics:
    article_count: int
    source_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    representative_article_id: uuid.UUID | None


@dataclass(frozen=True)
class Assignment:
    article_id: uuid.UUID
    cluster_id: uuid.UUID | None
    score: float
    algorithm_version: str
    reindex_article_ids: tuple[uuid.UUID, ...]
    merged_cluster_ids: tuple[uuid.UUID, ...]


def title_tokens(title: str, stop_words: frozenset[str]) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN_PATTERN.findall(title.casefold()) if token not in stop_words
    )


def jaccard[T](left: frozenset[T], right: frozenset[T]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def time_proximity(
    left: datetime, right: datetime, window: timedelta = CLUSTER_TIME_WINDOW
) -> float:
    distance = abs((left - right).total_seconds())
    return max(0.0, 1.0 - distance / window.total_seconds())


def score_articles(
    target: ArticleFeatures, candidate: ArticleFeatures, *, stop_words: frozenset[str]
) -> float:
    title = jaccard(
        title_tokens(target.title, stop_words), title_tokens(candidate.title, stop_words)
    )
    entities = jaccard(target.entity_ids, candidate.entity_ids)
    proximity = time_proximity(target.effective_date, candidate.effective_date)
    return TITLE_WEIGHT * title + ENTITY_WEIGHT * entities + TIME_WEIGHT * proximity


def shares_source(target: ArticleFeatures, candidate: ArticleFeatures) -> bool:
    """The same outlet covering the same story twice is not related reporting."""
    return bool(target.feed_ids & candidate.feed_ids)


def rank_candidates(
    target: ArticleFeatures,
    candidates: Iterable[ArticleFeatures],
    *,
    stop_words: frozenset[str],
    threshold: float = CLUSTER_SCORE_THRESHOLD,
) -> list[ScoredCandidate]:
    scored = [
        ScoredCandidate(candidate, score_articles(target, candidate, stop_words=stop_words))
        for candidate in candidates
        if candidate.article_id != target.article_id and not shares_source(target, candidate)
    ]
    return sorted(
        (item for item in scored if item.score >= threshold),
        key=lambda item: (
            -item.score,
            abs((item.features.effective_date - target.effective_date).total_seconds()),
            item.features.article_id,
        ),
    )


def select_survivor(clusters: Sequence[ClusterSummary]) -> ClusterSummary:
    """The smaller cluster merges into the larger one; ties go to the older cluster."""
    return sorted(
        clusters, key=lambda item: (-item.article_count, item.created_at, item.id)
    )[0]


def cluster_statistics(members: Iterable[ArticleFeatures]) -> ClusterStatistics:
    rows = sorted(members, key=lambda item: (item.effective_date, item.article_id))
    if not rows:
        return ClusterStatistics(0, 0, None, None, None)
    sources: set[uuid.UUID] = set()
    for row in rows:
        sources |= row.feed_ids
    return ClusterStatistics(
        article_count=len(rows),
        source_count=len(sources),
        first_published_at=rows[0].effective_date,
        last_published_at=rows[-1].effective_date,
        representative_article_id=rows[0].article_id,
    )


def _effective_date() -> ColumnElement[datetime]:
    return func.coalesce(Article.published_at, Article.first_discovered_at)


async def load_features(
    db: AsyncSession, article_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ArticleFeatures]:
    if not article_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Article.id,
                Article.title,
                Article.normalized_title_hash,
                _effective_date(),
            ).where(Article.id.in_(article_ids))
        )
    ).all()
    feeds: dict[uuid.UUID, set[uuid.UUID]] = {}
    for article_id, feed_id in (
        await db.execute(
            select(FeedArticle.article_id, FeedArticle.feed_id).where(
                FeedArticle.article_id.in_(article_ids)
            )
        )
    ).all():
        feeds.setdefault(article_id, set()).add(feed_id)
    entities: dict[uuid.UUID, set[uuid.UUID]] = {}
    for article_id, entity_id in (
        await db.execute(
            select(ArticleEntity.article_id, ArticleEntity.entity_id)
            .join(Entity, Entity.id == ArticleEntity.entity_id)
            .where(
                ArticleEntity.article_id.in_(article_ids),
                ArticleEntity.is_current.is_(True),
                Entity.entity_type.in_(CLUSTER_ENTITY_TYPES),
            )
        )
    ).all():
        entities.setdefault(article_id, set()).add(entity_id)
    return {
        row[0]: ArticleFeatures(
            article_id=row[0],
            title=row[1],
            normalized_title_hash=row[2],
            effective_date=row[3],
            entity_ids=frozenset(entities.get(row[0], ())),
            feed_ids=frozenset(feeds.get(row[0], ())),
        )
        for row in rows
    }


async def candidate_ids(db: AsyncSession, target: ArticleFeatures) -> list[uuid.UUID]:
    effective = _effective_date()
    matches = [Article.normalized_title_hash == target.normalized_title_hash]
    if target.entity_ids:
        shared_entities = (
            select(ArticleEntity.article_id)
            .where(
                ArticleEntity.entity_id.in_(target.entity_ids),
                ArticleEntity.is_current.is_(True),
                ArticleEntity.article_id != target.article_id,
            )
            .group_by(ArticleEntity.article_id)
            .having(
                func.count(func.distinct(ArticleEntity.entity_id))
                >= CLUSTER_MINIMUM_SHARED_ENTITIES
            )
        )
        matches.append(Article.id.in_(shared_entities))
    query = (
        select(Article.id)
        .where(
            Article.id != target.article_id,
            effective >= target.effective_date - CLUSTER_TIME_WINDOW,
            effective <= target.effective_date + CLUSTER_TIME_WINDOW,
            or_(*matches),
        )
        .order_by(
            func.abs(func.extract("epoch", effective - target.effective_date)),
            Article.id,
        )
        .limit(CLUSTER_CANDIDATE_LIMIT)
    )
    return list((await db.scalars(query)).all())


async def _member_ids(db: AsyncSession, cluster_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await db.scalars(
                select(StoryClusterMember.article_id).where(
                    StoryClusterMember.cluster_id == cluster_id
                )
            )
        ).all()
    )


async def _refresh_cluster(db: AsyncSession, cluster_id: uuid.UUID, version: str) -> bool:
    """Recompute cluster facts, dissolving the cluster when it no longer holds two members."""
    members = await _member_ids(db, cluster_id)
    if len(members) < 2:
        await db.execute(
            delete(StoryClusterMember).where(StoryClusterMember.cluster_id == cluster_id)
        )
        await db.execute(delete(StoryCluster).where(StoryCluster.id == cluster_id))
        return False
    cluster = await db.get(StoryCluster, cluster_id, with_for_update=True)
    if cluster is None:
        return False
    statistics = cluster_statistics((await load_features(db, members)).values())
    cluster.algorithm_version = version
    cluster.article_count = statistics.article_count
    cluster.source_count = statistics.source_count
    cluster.first_published_at = statistics.first_published_at
    cluster.last_published_at = statistics.last_published_at
    cluster.representative_article_id = statistics.representative_article_id
    await db.flush()
    return True


@runtime_checkable
class StoryClusterer(Protocol):
    version: str

    async def assign(self, db: AsyncSession, article_id: uuid.UUID) -> Assignment: ...


class RuleClusterer:
    """Postgres-only clustering: shared title hash or shared entities, then scored."""

    version = CLUSTER_ALGORITHM_VERSION

    async def assign(self, db: AsyncSession, article_id: uuid.UUID) -> Assignment:
        await db.execute(text(f"SELECT pg_advisory_xact_lock({CLUSTERING_ADVISORY_LOCK})"))
        target = (await load_features(db, [article_id])).get(article_id)
        if target is None:
            raise LookupError("article not found")
        stop_words = frozenset((await current_stop_words(db)).words)
        candidates = await load_features(db, await candidate_ids(db, target))
        ranked = rank_candidates(target, candidates.values(), stop_words=stop_words)
        member = await db.get(StoryClusterMember, article_id, with_for_update=True)
        previous_cluster_id = member.cluster_id if member is not None else None
        touched: set[uuid.UUID] = {article_id}
        if not ranked:
            if member is not None:
                await db.delete(member)
                await db.flush()
            return await self._finish(
                db,
                article_id=article_id,
                cluster_id=None,
                score=0.0,
                touched=touched,
                stale_cluster_ids={previous_cluster_id} if previous_cluster_id else set(),
                merged=(),
                changed=member is not None,
            )
        best = ranked[0]
        matched_clusters = {
            row.article_id: row.cluster_id
            for row in (
                await db.scalars(
                    select(StoryClusterMember)
                    .where(
                        StoryClusterMember.article_id.in_(
                            [item.features.article_id for item in ranked]
                        )
                    )
                    .with_for_update()
                )
            ).all()
        }
        base_cluster_id = matched_clusters.get(best.features.article_id)
        created_cluster = base_cluster_id is None
        if base_cluster_id is None:
            cluster = StoryCluster(algorithm_version=self.version)
            db.add(cluster)
            await db.flush()
            base_cluster_id = cluster.id
            db.add(
                StoryClusterMember(
                    article_id=best.features.article_id,
                    cluster_id=base_cluster_id,
                    score=best.score,
                    algorithm_version=self.version,
                )
            )
            await db.flush()
            touched.add(best.features.article_id)
        involved = {base_cluster_id} | set(matched_clusters.values())
        survivor_id, merged = await self._merge(db, involved, touched)
        member = await db.get(StoryClusterMember, article_id, with_for_update=True)
        if member is None:
            db.add(
                StoryClusterMember(
                    article_id=article_id,
                    cluster_id=survivor_id,
                    score=best.score,
                    algorithm_version=self.version,
                )
            )
        else:
            member.cluster_id = survivor_id
            member.score = best.score
            member.algorithm_version = self.version
        await db.flush()
        stale = {survivor_id}
        if previous_cluster_id is not None and previous_cluster_id not in merged:
            stale.add(previous_cluster_id)
        return await self._finish(
            db,
            article_id=article_id,
            cluster_id=survivor_id,
            score=best.score,
            touched=touched,
            stale_cluster_ids=stale,
            merged=merged,
            changed=(
                member is None
                or created_cluster
                or bool(merged)
                or previous_cluster_id != survivor_id
            ),
        )

    async def _merge(
        self, db: AsyncSession, cluster_ids: set[uuid.UUID], touched: set[uuid.UUID]
    ) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
        summaries = [
            ClusterSummary(row.id, row.created_at, row.article_count)
            for row in (
                await db.scalars(
                    select(StoryCluster)
                    .where(StoryCluster.id.in_(cluster_ids))
                    .order_by(StoryCluster.id)
                    .with_for_update()
                )
            ).all()
        ]
        # A cluster created moments ago still counts zero members, which keeps a fresh
        # pair from absorbing an established cluster — the same outcome the tie rule gives.
        survivor = select_survivor(summaries)
        merged: list[uuid.UUID] = []
        for summary in sorted(summaries, key=lambda item: item.id):
            if summary.id == survivor.id:
                continue
            touched.update(await _member_ids(db, summary.id))
            await db.execute(
                update(StoryClusterMember)
                .where(StoryClusterMember.cluster_id == summary.id)
                .values(cluster_id=survivor.id, algorithm_version=self.version)
            )
            await db.execute(delete(StoryCluster).where(StoryCluster.id == summary.id))
            merged.append(summary.id)
        await db.flush()
        return survivor.id, tuple(merged)

    async def _finish(
        self,
        db: AsyncSession,
        *,
        article_id: uuid.UUID,
        cluster_id: uuid.UUID | None,
        score: float,
        touched: set[uuid.UUID],
        stale_cluster_ids: set[uuid.UUID],
        merged: tuple[uuid.UUID, ...],
        changed: bool,
    ) -> Assignment:
        surviving = cluster_id
        for stale_id in sorted(stale_cluster_ids):
            touched.update(await _member_ids(db, stale_id))
            if not await _refresh_cluster(db, stale_id, self.version) and stale_id == cluster_id:
                surviving = None
        return Assignment(
            article_id=article_id,
            cluster_id=surviving,
            score=score,
            algorithm_version=self.version,
            # An assignment that changed nothing has nothing to reindex.
            reindex_article_ids=tuple(sorted(touched)) if changed else (),
            merged_cluster_ids=merged,
        )
