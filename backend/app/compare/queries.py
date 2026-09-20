import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    CTE,
    ColumnElement,
    Select,
    and_,
    cast,
    exists,
    func,
    literal_column,
    not_,
    or_,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.sqltypes import Date

from app.clustering.models import StoryCluster, StoryClusterMember
from app.compare.schemas import (
    CompareArticlePage,
    CompareClusterPage,
    CompareClusterResponse,
    CompareDay,
    CompareOverlap,
    CompareRelated,
    CompareResponse,
    CompareSide,
    Kind,
    Overlap,
    Part,
    RelatedCountry,
    RelatedItem,
    Role,
    Subject,
)
from app.entities.queries import effective_date, load_representatives, window_start
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import article_response, encode_cursor
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity
from app.sources.queries import scoped_articles

# The two country annotation roles; the `source` role is the feed's own country instead.
ANNOTATION_ROLE = {"story": "primary", "mentioned": "mentioned"}
TOP = 10

__all__ = ["Spec", "subject_articles", "window_start"]


@dataclass(frozen=True)
class Spec:
    kind: Kind
    a: str
    b: str
    role: Role | None
    days: int


def subject_articles(kind: Kind, ref: str, role: Role | None, start: datetime) -> Select[Any]:
    """One subject's distinct articles dated in the window, as `(article_id, at)`.

    Every metric and every evidence page is built from this, so both subjects are always measured
    the same way. `ref` is an id (entity, source) or an upper-case country code.
    """
    effective = effective_date()
    base = select(Article.id.label("article_id"), effective.label("at"))
    if kind == "entity":
        return (
            base.join(ArticleEntity, ArticleEntity.article_id == Article.id)
            .where(
                ArticleEntity.entity_id == uuid.UUID(ref),
                ArticleEntity.is_current.is_(True),
                effective >= start,
            )
            .distinct()
        )
    if kind == "source":
        found = scoped_articles(uuid.UUID(ref), start).subquery()
        return select(found.c.article_id, found.c.at)
    if role == "source":
        return (
            base.join(FeedArticle, FeedArticle.article_id == Article.id)
            .join(Feed, Feed.id == FeedArticle.feed_id)
            .where(func.upper(Feed.source_country) == ref, effective >= start)
            .distinct()
        )
    return (
        base.join(ArticleCountryAnnotation, ArticleCountryAnnotation.article_id == Article.id)
        .where(
            ArticleCountryAnnotation.country_code == ref,
            ArticleCountryAnnotation.role == ANNOTATION_ROLE[role or "story"],
            ArticleCountryAnnotation.is_current.is_(True),
            effective >= start,
        )
        .distinct()
    )


def _sides(spec: Spec, start: datetime) -> tuple[CTE, CTE]:
    return (
        subject_articles(spec.kind, spec.a, spec.role, start).cte("side_a"),
        subject_articles(spec.kind, spec.b, spec.role, start).cte("side_b"),
    )


async def resolve(db: AsyncSession, spec: Spec, ref: str) -> Subject | None:
    label, retired = ref, False
    if spec.kind == "entity":
        entity = await db.get(Entity, uuid.UUID(ref))
        if entity is None:
            return None
        label = entity.display_text
    elif spec.kind == "source":
        feed = await db.get(Feed, uuid.UUID(ref))
        if feed is None:
            return None
        label, retired = feed.name, feed.retired_at is not None
    return Subject(kind=spec.kind, ref=ref, label=label, role=spec.role, retired=retired)


async def _overlap(db: AsyncSession, a: Select[Any], b: Select[Any]) -> Overlap:
    """Sizes of A only, both and B only, from one full join of two single-column key sets."""
    left, right = a.cte(), b.cte()
    only_a, both, only_b = (
        await db.execute(
            select(
                func.count().filter(right.c.k.is_(None)),
                func.count().filter(left.c.k.is_not(None), right.c.k.is_not(None)),
                func.count().filter(left.c.k.is_(None)),
            ).select_from(left.join(right, left.c.k == right.c.k, full=True))
        )
    ).one()
    union = int(only_a + both + only_b)
    return Overlap(
        only_a=int(only_a),
        both=int(both),
        only_b=int(only_b),
        union=union,
        jaccard=both / union if union else None,
    )


def _articles_of(side: CTE) -> Select[Any]:
    return select(side.c.article_id.label("k"))


def _stories_of(side: CTE) -> Select[Any]:
    return (
        select(StoryClusterMember.cluster_id.label("k"))
        .join(side, side.c.article_id == StoryClusterMember.article_id)
        .distinct()
    )


def _sources_of(side: CTE) -> Select[Any]:
    return (
        select(FeedArticle.feed_id.label("k"))
        .join(side, side.c.article_id == FeedArticle.article_id)
        .distinct()
    )


async def _timelines(
    db: AsyncSession, side_a: CTE, side_b: CTE, start: datetime, days: int
) -> tuple[list[CompareDay], list[CompareDay]]:
    tagged = union_all(
        select(literal_column("'a'").label("side"), side_a.c.at.label("at")),
        select(literal_column("'b'").label("side"), side_b.c.at.label("at")),
    ).subquery()
    day = cast(func.date_trunc(literal_column("'day'"), func.timezone("UTC", tagged.c.at)), Date)
    counts: dict[tuple[str, date], int] = {
        (row.side, row.day): row.n
        for row in await db.execute(
            select(tagged.c.side, day.label("day"), func.count().label("n")).group_by(
                tagged.c.side, day
            )
        )
    }
    first_day = start.date()
    return tuple(  # type: ignore[return-value]
        [
            CompareDay(
                date=first_day + timedelta(days=offset),
                article_count=counts.get((side, first_day + timedelta(days=offset)), 0),
            )
            for offset in range(days)
        ]
        for side in ("a", "b")
    )


async def _related(db: AsyncSession, spec: Spec, side_a: CTE, side_b: CTE) -> CompareRelated:
    scope = union_all(
        select(
            side_a.c.article_id.label("article_id"),
            literal_column("1").label("in_a"),
            literal_column("0").label("in_b"),
        ),
        select(side_b.c.article_id, literal_column("0"), literal_column("1")),
    ).cte("scope")
    a_count = func.count(func.distinct(scope.c.article_id)).filter(scope.c.in_a == 1)
    b_count = func.count(func.distinct(scope.c.article_id)).filter(scope.c.in_b == 1)
    ranking = (a_count + b_count).desc()

    subject_ids = [uuid.UUID(spec.a), uuid.UUID(spec.b)] if spec.kind != "country" else []
    entity_rows = (
        await db.execute(
            select(Entity, a_count.label("a"), b_count.label("b"))
            .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
            .join(scope, scope.c.article_id == ArticleEntity.article_id)
            .where(
                ArticleEntity.is_current.is_(True),
                *([Entity.id.not_in(subject_ids)] if spec.kind == "entity" else []),
            )
            .group_by(Entity.id)
            .order_by(ranking, Entity.id)
            .limit(TOP)
        )
    ).all()

    country = ArticleCountryAnnotation
    own: list[ColumnElement[bool]] = []
    if spec.kind == "country" and spec.role in ANNOTATION_ROLE:
        own = [
            not_(
                and_(
                    country.country_code.in_([spec.a, spec.b]),
                    country.role == ANNOTATION_ROLE[spec.role or "story"],
                )
            )
        ]
    country_rows = (
        await db.execute(
            select(country.country_code, country.role, a_count.label("a"), b_count.label("b"))
            .join(scope, scope.c.article_id == country.article_id)
            .where(country.is_current.is_(True), *own)
            .group_by(country.country_code, country.role)
            .order_by(ranking, country.role, country.country_code)
            .limit(TOP)
        )
    ).all()

    sources: list[RelatedItem] | None = None
    if spec.kind != "source":
        feed_rows = (
            await db.execute(
                select(Feed, a_count.label("a"), b_count.label("b"))
                .join(FeedArticle, FeedArticle.feed_id == Feed.id)
                .join(scope, scope.c.article_id == FeedArticle.article_id)
                .group_by(Feed.id)
                .order_by(ranking, Feed.id)
                .limit(TOP)
            )
        ).all()
        sources = [
            RelatedItem(id=row.Feed.id, label=row.Feed.name, a_articles=row.a, b_articles=row.b)
            for row in feed_rows
        ]
    return CompareRelated(
        entities=[
            RelatedItem(
                id=row.Entity.id, label=row.Entity.display_text, a_articles=row.a, b_articles=row.b
            )
            for row in entity_rows
        ],
        countries=[
            RelatedCountry(country_code=row[0], role=row[1], a_articles=row.a, b_articles=row.b)
            for row in country_rows
        ],
        sources=sources,
    )


async def summary(db: AsyncSession, spec: Spec, a: Subject, b: Subject) -> CompareResponse:
    start = window_start(spec.days)
    side_a, side_b = _sides(spec, start)
    articles = await _overlap(db, _articles_of(side_a), _articles_of(side_b))
    stories = await _overlap(db, _stories_of(side_a), _stories_of(side_b))
    sources = (
        await _overlap(db, _sources_of(side_a), _sources_of(side_b))
        if spec.kind != "source"
        else None
    )
    timeline_a, timeline_b = await _timelines(db, side_a, side_b, start, spec.days)
    related = await _related(db, spec, side_a, side_b)

    def side(subject: Subject, own: str, timeline: list[CompareDay]) -> CompareSide:
        # A side's total is its "only" part plus the shared part.
        return CompareSide(
            subject=subject,
            articles=articles.both + getattr(articles, own),
            stories=stories.both + getattr(stories, own),
            sources=sources.both + getattr(sources, own) if sources else None,
            timeline=timeline,
        )

    return CompareResponse(
        kind=spec.kind,
        role=spec.role,
        window_days=spec.days,
        a=side(a, "only_a", timeline_a),
        b=side(b, "only_b", timeline_b),
        overlap=CompareOverlap(articles=articles, stories=stories, sources=sources),
        related=related,
    )


def _part_of(mine: CTE, other: CTE, column: str, part: Part) -> Select[Any]:
    """`part` = 'b' is taken from B's side (`mine` is B, `other` is A)."""
    hit = exists().where(other.c[column] == mine.c[column])
    return select(mine.c[column]).where(hit if part == "both" else not_(hit))


def _article_ids(side_a: CTE, side_b: CTE, part: Part) -> Select[Any]:
    if part == "b":
        return _part_of(side_b, side_a, "article_id", part)
    return _part_of(side_a, side_b, "article_id", part)


async def articles(
    db: AsyncSession,
    spec: Spec,
    part: Part,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None,
) -> CompareArticlePage:
    side_a, side_b = _sides(spec, window_start(spec.days))
    effective = effective_date()
    query = (
        select(Article, effective.label("effective_date"))
        .where(Article.id.in_(_article_ids(side_a, side_b, part)))
        .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        .order_by(effective.desc(), Article.id.desc())
    )
    if cursor:
        timestamp, item_id = cursor
        query = query.where(
            or_(effective < timestamp, and_(effective == timestamp, Article.id < item_id))
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    next_cursor = (
        encode_cursor(rows[limit - 1].effective_date, rows[limit - 1].Article.id)
        if len(rows) > limit
        else None
    )
    return CompareArticlePage(
        items=[article_response(row.Article) for row in rows[:limit]], next_cursor=next_cursor
    )


def _story_counts(side: CTE, name: str) -> CTE:
    return (
        select(
            StoryClusterMember.cluster_id.label("cluster_id"),
            func.count(func.distinct(StoryClusterMember.article_id)).label("n"),
        )
        .join(side, side.c.article_id == StoryClusterMember.article_id)
        .group_by(StoryClusterMember.cluster_id)
        .cte(name)
    )


async def clusters(
    db: AsyncSession,
    spec: Spec,
    part: Part,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None,
) -> CompareClusterPage:
    side_a, side_b = _sides(spec, window_start(spec.days))
    in_a, in_b = _story_counts(side_a, "stories_a"), _story_counts(side_b, "stories_b")
    if part == "b":
        ids = _part_of(in_b, in_a, "cluster_id", part)
    else:
        ids = _part_of(in_a, in_b, "cluster_id", part)
    recency = func.coalesce(StoryCluster.last_published_at, StoryCluster.created_at)
    query = (
        select(StoryCluster, recency.label("recency"))
        .where(StoryCluster.id.in_(ids))
        .order_by(recency.desc(), StoryCluster.id.desc())
    )
    if cursor:
        timestamp, item_id = cursor
        query = query.where(
            or_(recency < timestamp, and_(recency == timestamp, StoryCluster.id < item_id))
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    page = [row.StoryCluster for row in rows[:limit]]
    key = func.coalesce(in_a.c.cluster_id, in_b.c.cluster_id)
    counts = {
        row.cluster_id: (int(row.a), int(row.b))
        for row in await db.execute(
            select(
                key.label("cluster_id"),
                func.coalesce(in_a.c.n, 0).label("a"),
                func.coalesce(in_b.c.n, 0).label("b"),
            )
            .select_from(in_a.join(in_b, in_a.c.cluster_id == in_b.c.cluster_id, full=True))
            .where(key.in_([cluster.id for cluster in page]))
        )
    }
    representatives = await load_representatives(db, [c.representative_article_id for c in page])
    next_cursor = (
        encode_cursor(rows[limit - 1].recency, rows[limit - 1].StoryCluster.id)
        if len(rows) > limit
        else None
    )
    return CompareClusterPage(
        items=[
            CompareClusterResponse(
                id=cluster.id,
                article_count=cluster.article_count,
                source_count=cluster.source_count,
                first_published_at=cluster.first_published_at,
                last_published_at=cluster.last_published_at,
                representative_article=(
                    article_response(representatives[cluster.representative_article_id])
                    if cluster.representative_article_id in representatives
                    else None
                ),
                a_articles=counts[cluster.id][0],
                b_articles=counts[cluster.id][1],
            )
            for cluster in page
        ],
        next_cursor=next_cursor,
    )
