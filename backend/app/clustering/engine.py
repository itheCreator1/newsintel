"""Replaceable story clustering.

The default clusterer uses PostgreSQL rules only, so related reporting keeps
working while Elasticsearch is unavailable.
"""

import dataclasses
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import ColumnElement, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import ArticleClusterTerm, StoryCluster, StoryClusterMember
from app.feeds.models import Article, FeedArticle
from app.nlp.models import ArticleEntity, Entity
from app.nlp.service import current_stop_words, load_input_document

CLUSTER_ALGORITHM_VERSION = "rule-2"
CLUSTER_TIME_WINDOW = timedelta(hours=48)
CLUSTER_CANDIDATE_LIMIT = 200
CLUSTER_SCORE_THRESHOLD = 0.45
CLUSTER_ENTITY_TYPES = ("ORG", "PERSON", "GPE", "EVENT")
CLUSTER_MINIMUM_SHARED_ENTITIES = 2
TITLE_WEIGHT = 0.5
ENTITY_WEIGHT = 0.35
TIME_WEIGHT = 0.15
# Wording: outlets rewording one story rarely share a headline, but their opening paragraphs
# keep the same specifics (the barges, the unpaid invoices, the breakwater). The title and
# the first words of the body (or the feed summary) are reduced to stemmed terms, each
# weighted by how rare it is inside the clustering window.
WORDING_WEIGHT = 1.0 - TIME_WEIGHT
LEDE_WORD_LIMIT = 80
TERM_MINIMUM_LENGTH = 3
TERM_MAXIMUM_LENGTH = 64
CLUSTER_MINIMUM_SHARED_TERMS = 4
CLUSTER_TERM_CANDIDATE_LIMIT = 100
# Shared weight is divided by the smaller term set, clamped: a short summary cannot look like
# a match on a handful of words, and a long lede needs no more than this many to make one.
TERM_OVERLAP_FLOOR = 12
TERM_OVERLAP_CEILING = 25
# A window this small cannot tell a rare term from a common one, so rarity is measured as if
# the window held at least this many articles.
TERM_WEIGHT_MINIMUM_WINDOW = 100
# Clustering writes span articles, so every assignment serialises behind one lock.
CLUSTERING_ADVISORY_LOCK = 728341906

_TOKEN_PATTERN = re.compile(r"[0-9a-z]+")
_WORD_PATTERN = re.compile(r"[^\W\d_]+")


@dataclass(frozen=True)
class ArticleFeatures:
    article_id: uuid.UUID
    title: str
    normalized_title_hash: str
    effective_date: datetime
    entity_ids: frozenset[uuid.UUID]
    feed_ids: frozenset[uuid.UUID]
    terms: frozenset[str] = frozenset()


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


def stem(word: str) -> str:
    """Strip common English inflections so "votes", "voted" and "voting" meet as "vot".

    Deliberately small: it only has to agree with itself, not produce dictionary words.
    """
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            # "planned" -> "plann" -> "plan", but "called" keeps its double l.
            if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "lsz":
                word = word[:-1]
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def lede_terms(
    text: str, stop_words: frozenset[str], *, word_limit: int = LEDE_WORD_LIMIT
) -> frozenset[str]:
    """Stemmed content terms from the first words of an article (its title comes first)."""
    words = _WORD_PATTERN.findall(text.casefold())[:word_limit]
    return frozenset(
        stem(word)[:TERM_MAXIMUM_LENGTH]
        for word in words
        if len(word) >= TERM_MINIMUM_LENGTH and word not in stop_words
    )


def term_weights(
    document_frequencies: Mapping[str, int], window_articles: int
) -> dict[str, float]:
    """Inverse document frequency inside the window, scaled to [0, 1].

    A term only one article in the window uses weighs 1; a term every article uses weighs 0.
    """
    window = max(window_articles, TERM_WEIGHT_MINIMUM_WINDOW)
    scale = math.log(window)
    return {
        term: max(0.0, math.log(window / max(frequency, 1)) / scale)
        for term, frequency in document_frequencies.items()
    }


def term_overlap(
    left: frozenset[str], right: frozenset[str], weights: Mapping[str, float]
) -> float:
    shared = left & right
    if len(shared) < CLUSTER_MINIMUM_SHARED_TERMS:
        return 0.0
    denominator = min(
        max(min(len(left), len(right)), TERM_OVERLAP_FLOOR), TERM_OVERLAP_CEILING
    )
    return min(1.0, sum(weights.get(term, 0.0) for term in shared) / denominator)


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
    target: ArticleFeatures,
    candidate: ArticleFeatures,
    *,
    stop_words: frozenset[str],
    weights: Mapping[str, float] | None = None,
) -> float:
    """The better of two rules: headline and entities, or shared opening wording.

    Each rule is time-weighted the same way, so either can carry a pair on its own.
    """
    title = jaccard(
        title_tokens(target.title, stop_words), title_tokens(candidate.title, stop_words)
    )
    entities = jaccard(target.entity_ids, candidate.entity_ids)
    proximity = time_proximity(target.effective_date, candidate.effective_date)
    headline = TITLE_WEIGHT * title + ENTITY_WEIGHT * entities + TIME_WEIGHT * proximity
    if not weights:
        return headline
    wording = WORDING_WEIGHT * term_overlap(target.terms, candidate.terms, weights)
    return max(headline, wording + TIME_WEIGHT * proximity)


def shares_source(target: ArticleFeatures, candidate: ArticleFeatures) -> bool:
    """The same outlet covering the same story twice is not related reporting."""
    return bool(target.feed_ids & candidate.feed_ids)


def rank_candidates(
    target: ArticleFeatures,
    candidates: Iterable[ArticleFeatures],
    *,
    stop_words: frozenset[str],
    weights: Mapping[str, float] | None = None,
    threshold: float = CLUSTER_SCORE_THRESHOLD,
) -> list[ScoredCandidate]:
    scored = [
        ScoredCandidate(
            candidate,
            score_articles(target, candidate, stop_words=stop_words, weights=weights),
        )
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


def _window(target: ArticleFeatures) -> tuple[datetime, datetime]:
    return (
        target.effective_date - CLUSTER_TIME_WINDOW,
        target.effective_date + CLUSTER_TIME_WINDOW,
    )


async def store_terms(db: AsyncSession, target: ArticleFeatures, terms: frozenset[str]) -> None:
    """Replace the article's indexed wording with the terms it has now."""
    await db.execute(
        delete(ArticleClusterTerm).where(ArticleClusterTerm.article_id == target.article_id)
    )
    if terms:
        db.add_all(
            ArticleClusterTerm(
                article_id=target.article_id, term=term, effective_at=target.effective_date
            )
            for term in sorted(terms)
        )
    await db.flush()


async def load_terms(
    db: AsyncSession, article_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, frozenset[str]]:
    if not article_ids:
        return {}
    terms: dict[uuid.UUID, set[str]] = {}
    for article_id, term in (
        await db.execute(
            select(ArticleClusterTerm.article_id, ArticleClusterTerm.term).where(
                ArticleClusterTerm.article_id.in_(article_ids)
            )
        )
    ).all():
        terms.setdefault(article_id, set()).add(term)
    return {article_id: frozenset(values) for article_id, values in terms.items()}


async def window_term_weights(db: AsyncSession, target: ArticleFeatures) -> dict[str, float]:
    """Weigh the target's terms by how many articles in its window share each one."""
    if not target.terms:
        return {}
    start, end = _window(target)
    effective = _effective_date()
    window_articles = await db.scalar(
        select(func.count()).select_from(Article).where(effective >= start, effective <= end)
    )
    frequencies = {
        term: int(count)
        for term, count in (
            await db.execute(
                select(ArticleClusterTerm.term, func.count())
                .where(
                    ArticleClusterTerm.term.in_(target.terms),
                    ArticleClusterTerm.effective_at >= start,
                    ArticleClusterTerm.effective_at <= end,
                )
                .group_by(ArticleClusterTerm.term)
            )
        ).all()
    }
    return term_weights(
        {term: frequencies.get(term, 1) for term in target.terms}, int(window_articles or 0)
    )


async def wording_candidate_ids(db: AsyncSession, target: ArticleFeatures) -> list[uuid.UUID]:
    """Articles in the window sharing the most opening terms with the target."""
    if len(target.terms) < CLUSTER_MINIMUM_SHARED_TERMS:
        return []
    start, end = _window(target)
    shared = func.count()
    query = (
        select(ArticleClusterTerm.article_id)
        .where(
            ArticleClusterTerm.term.in_(target.terms),
            ArticleClusterTerm.effective_at >= start,
            ArticleClusterTerm.effective_at <= end,
            ArticleClusterTerm.article_id != target.article_id,
        )
        .group_by(ArticleClusterTerm.article_id)
        .having(shared >= CLUSTER_MINIMUM_SHARED_TERMS)
        .order_by(shared.desc(), ArticleClusterTerm.article_id)
        .limit(CLUSTER_TERM_CANDIDATE_LIMIT)
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
    """Postgres-only clustering: shared title hash, entities or opening wording, then scored."""

    version = CLUSTER_ALGORITHM_VERSION

    async def assign(self, db: AsyncSession, article_id: uuid.UUID) -> Assignment:
        await db.execute(text(f"SELECT pg_advisory_xact_lock({CLUSTERING_ADVISORY_LOCK})"))
        target = (await load_features(db, [article_id])).get(article_id)
        if target is None:
            raise LookupError("article not found")
        stop_words = frozenset((await current_stop_words(db)).words)
        document = await load_input_document(db, article_id)
        target = dataclasses.replace(target, terms=lede_terms(document.text, stop_words))
        await store_terms(db, target, target.terms)
        weights = await window_term_weights(db, target)
        found = dict.fromkeys(await candidate_ids(db, target))
        found.update(dict.fromkeys(await wording_candidate_ids(db, target)))
        candidates = await load_features(db, list(found))
        terms = await load_terms(db, list(candidates))
        ranked = rank_candidates(
            target,
            (
                dataclasses.replace(item, terms=terms.get(item.article_id, frozenset()))
                for item in candidates.values()
            ),
            stop_words=stop_words,
            weights=weights,
        )
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
