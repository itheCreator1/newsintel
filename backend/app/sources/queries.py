import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, cast, exists, extract, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.sqltypes import Date

from app.clustering.models import StoryCluster, StoryClusterMember
from app.entities.queries import effective_date, load_representatives, window_start
from app.entities.schemas import RelatedCountryResponse, RelatedEntityResponse
from app.feeds.models import (
    Article,
    ArticleContent,
    ArticleProcessingJob,
    Feed,
    FeedArticle,
    FeedFetch,
)
from app.feeds.schemas import FeedResponse, FetchPage, FetchResponse
from app.feeds.service import article_response, encode_cursor
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleLanguageAnnotation,
    Entity,
)
from app.sources.schemas import (
    SourceArticlePage,
    SourceClusterPage,
    SourceClusterResponse,
    SourceCoverage,
    SourceDetail,
    SourceExtraction,
    SourceFetches,
    SourceHealth,
    SourceLanguage,
    SourcePublishing,
    SourceTimelineDay,
    SourceTiming,
)

ACTIVE_JOB = ("queued", "running", "retrying")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def get_source(db: AsyncSession, source_id: uuid.UUID) -> Feed | None:
    return await db.get(Feed, source_id)


def scoped_articles(source_id: uuid.UUID, start: datetime) -> Select[Any]:
    """The source's articles whose date (`published_at`, else discovery) is in the window.

    An article several feeds carry counts for each of them; `(feed_id, article_id)` is unique, so it
    is never counted twice for one source.
    """
    effective = effective_date()
    return (
        select(
            Article.id.label("article_id"),
            effective.label("at"),
            Article.published_at.is_not(None).label("has_published"),
        )
        .join(FeedArticle, FeedArticle.article_id == Article.id)
        .where(FeedArticle.feed_id == source_id, effective >= start)
    )


def positions(
    source_id: uuid.UUID,
    start: datetime | None = None,
    cluster_ids: list[uuid.UUID] | None = None,
) -> Select[Any]:
    """Per story, this source's earliest article and how far behind the story's earliest it was.

    Stories are limited to those with an article of this source in the window (`start`) or to
    `cluster_ids`. The story's earliest article is by `(effective date, article id)`.
    """
    effective = effective_date()
    member = StoryClusterMember
    conditions = [FeedArticle.feed_id == source_id]
    if start is not None:
        conditions.append(effective >= start)
    if cluster_ids is not None:
        conditions.append(member.cluster_id.in_(cluster_ids))
    mine = (
        select(
            member.cluster_id.label("cluster_id"),
            Article.id.label("article_id"),
            effective.label("at"),
        )
        .join(Article, Article.id == member.article_id)
        .join(FeedArticle, FeedArticle.article_id == Article.id)
        .where(*conditions)
        .distinct(member.cluster_id)
        .order_by(member.cluster_id, effective, Article.id)
        .cte("mine")
    )
    ranked = (
        select(
            member.cluster_id.label("cluster_id"),
            Article.id.label("article_id"),
            effective.label("at"),
            func.row_number()
            .over(partition_by=member.cluster_id, order_by=(effective, Article.id))
            .label("rank"),
        )
        .join(Article, Article.id == member.article_id)
        .where(member.cluster_id.in_(select(mine.c.cluster_id)))
        .cte("ranked")
    )
    return (
        select(
            mine.c.cluster_id,
            mine.c.article_id,
            (mine.c.article_id == ranked.c.article_id).label("is_first"),
            (extract("epoch", mine.c.at - ranked.c.at) / 60).label("minutes"),
        )
        .select_from(mine)
        .join(ranked, and_(ranked.c.cluster_id == mine.c.cluster_id, ranked.c.rank == 1))
    )


async def detail(db: AsyncSession, feed: Feed, days: int) -> SourceDetail:
    start = window_start(days)
    scoped = scoped_articles(feed.id, start).cte("scoped")
    content = exists().where(ArticleContent.article_id == scoped.c.article_id)
    active = exists().where(
        ArticleProcessingJob.article_id == scoped.c.article_id,
        ArticleProcessingJob.status.in_(ACTIVE_JOB),
    )
    failed = exists().where(
        ArticleProcessingJob.article_id == scoped.c.article_id,
        ArticleProcessingJob.status == "failed",
    )
    total, published, extracted, in_progress, failed_count = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(scoped.c.has_published),
                func.count().filter(content),
                func.count().filter(~content, active),
                func.count().filter(~content, ~active, failed),
            ).select_from(scoped)
        )
    ).one()

    day = cast(func.date_trunc(literal_column("'day'"), func.timezone("UTC", scoped.c.at)), Date)
    counts: dict[date, int] = {
        row.day: row.n
        for row in await db.execute(
            select(day.label("day"), func.count().label("n")).select_from(scoped).group_by(day)
        )
    }
    first_day = start.date()
    timeline = [
        SourceTimelineDay(
            date=first_day + timedelta(days=offset),
            article_count=counts.get(first_day + timedelta(days=offset), 0),
        )
        for offset in range(days)
    ]

    first_seen, last_seen = (
        await db.execute(
            select(func.min(FeedArticle.discovered_at), func.max(FeedArticle.discovered_at)).where(
                FeedArticle.feed_id == feed.id
            )
        )
    ).one()

    in_window = and_(FeedFetch.feed_id == feed.id, FeedFetch.started_at >= start)
    fetches, ok, bad, new, duration = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(FeedFetch.status == "success"),
                func.count().filter(FeedFetch.status == "failed"),
                func.coalesce(func.sum(FeedFetch.new_article_count), 0),
                func.avg(FeedFetch.duration_ms),
            ).where(in_window)
        )
    ).one()
    categories = {
        row.error_category: row.n
        for row in await db.execute(
            select(FeedFetch.error_category, func.count().label("n"))
            .where(in_window, FeedFetch.status == "failed", FeedFetch.error_category.is_not(None))
            .group_by(FeedFetch.error_category)
        )
    }

    latest = select(FeedFetch).where(FeedFetch.feed_id == feed.id)
    last_attempt = await db.scalar(
        latest.order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc()).limit(1)
    )
    last_failure = await db.scalar(
        latest.where(FeedFetch.status == "failed")
        .order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc())
        .limit(1)
    )
    last_success = (
        select(func.max(FeedFetch.started_at))
        .where(FeedFetch.feed_id == feed.id, FeedFetch.status == "success")
        .scalar_subquery()
    )
    streak = await db.scalar(
        select(func.count()).where(
            FeedFetch.feed_id == feed.id,
            FeedFetch.status == "failed",
            FeedFetch.started_at > func.coalesce(last_success, EPOCH),
        )
    )
    return SourceDetail(
        **FeedResponse.model_validate(feed).model_dump(),
        retired_at=feed.retired_at,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        health=SourceHealth(
            last_attempt_at=last_attempt.started_at if last_attempt else None,
            last_attempt_status=last_attempt.status if last_attempt else None,
            last_failure=FetchResponse.model_validate(last_failure) if last_failure else None,
            consecutive_failures=int(streak or 0),
        ),
        window_days=days,
        publishing=SourcePublishing(articles=int(total), with_published_at=int(published)),
        extraction=SourceExtraction(
            articles=int(total),
            extracted=int(extracted),
            failed=int(failed_count),
            in_progress=int(in_progress),
            not_extracted=int(total - extracted - in_progress - failed_count),
        ),
        fetches=SourceFetches(
            total=int(fetches),
            success=int(ok),
            failed=int(bad),
            new_articles=int(new),
            mean_duration_ms=float(duration) if duration is not None else None,
            failures_by_category=categories,
        ),
        timeline=timeline,
    )


async def coverage(db: AsyncSession, source_id: uuid.UUID, days: int) -> SourceCoverage:
    scoped = scoped_articles(source_id, window_start(days)).cte("scoped")
    total = await db.scalar(select(func.count()).select_from(scoped))
    entity_count = func.count(func.distinct(ArticleEntity.article_id)).label("article_count")
    entity_rows = (
        await db.execute(
            select(Entity, entity_count)
            .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
            .join(scoped, scoped.c.article_id == ArticleEntity.article_id)
            .where(ArticleEntity.is_current.is_(True))
            .group_by(Entity.id)
            .order_by(entity_count.desc(), Entity.id.asc())
            .limit(10)
        )
    ).all()
    country_count = func.count(func.distinct(ArticleCountryAnnotation.article_id)).label("n")
    country_rows = (
        await db.execute(
            select(
                ArticleCountryAnnotation.country_code, ArticleCountryAnnotation.role, country_count
            )
            .join(scoped, scoped.c.article_id == ArticleCountryAnnotation.article_id)
            .where(ArticleCountryAnnotation.is_current.is_(True))
            .group_by(ArticleCountryAnnotation.country_code, ArticleCountryAnnotation.role)
            .order_by(
                country_count.desc(),
                ArticleCountryAnnotation.role.asc(),
                ArticleCountryAnnotation.country_code.asc(),
            )
            .limit(10)
        )
    ).all()
    language_count = func.count(func.distinct(ArticleLanguageAnnotation.article_id)).label("n")
    language_rows = (
        await db.execute(
            select(ArticleLanguageAnnotation.language, language_count)
            .join(scoped, scoped.c.article_id == ArticleLanguageAnnotation.article_id)
            .where(ArticleLanguageAnnotation.is_current.is_(True))
            .group_by(ArticleLanguageAnnotation.language)
            .order_by(language_count.desc(), ArticleLanguageAnnotation.language.asc())
            .limit(10)
        )
    ).all()
    return SourceCoverage(
        window_days=days,
        articles=int(total or 0),
        entities=[
            RelatedEntityResponse(
                id=item.id,
                display_name=item.display_text,
                normalized_text=item.normalized_text,
                language=item.language,
                entity_type=item.entity_type,
                article_count=int(count),
            )
            for item, count in entity_rows
        ],
        countries=[
            RelatedCountryResponse(country_code=code, role=role, article_count=int(count))
            for code, role, count in country_rows
        ],
        languages=[
            SourceLanguage(language=name, article_count=int(n)) for name, n in language_rows
        ],
    )


async def timing(db: AsyncSession, source_id: uuid.UUID, days: int) -> SourceTiming:
    found = positions(source_id, window_start(days)).subquery()
    behind = ~found.c.is_first
    stories, first, median, p90 = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(found.c.is_first),
                func.percentile_cont(0.5).within_group(found.c.minutes).filter(behind),
                func.percentile_cont(0.9).within_group(found.c.minutes).filter(behind),
            )
            .select_from(found)
            .join(StoryCluster, StoryCluster.id == found.c.cluster_id)
            .where(StoryCluster.source_count >= 2)
        )
    ).one()
    return SourceTiming(
        window_days=days,
        stories=int(stories),
        first=int(first),
        median_minutes_behind=float(median) if median is not None else None,
        p90_minutes_behind=float(p90) if p90 is not None else None,
    )


async def articles(
    db: AsyncSession, source_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> SourceArticlePage:
    effective = effective_date()
    query = (
        select(Article, effective.label("effective_date"))
        .join(FeedArticle, FeedArticle.article_id == Article.id)
        .where(FeedArticle.feed_id == source_id)
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
    return SourceArticlePage(
        items=[article_response(row.Article) for row in rows[:limit]], next_cursor=next_cursor
    )


async def clusters(
    db: AsyncSession, source_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> SourceClusterPage:
    recency = func.coalesce(StoryCluster.last_published_at, StoryCluster.created_at)
    query = (
        select(StoryCluster, recency.label("recency"))
        .join(StoryClusterMember, StoryClusterMember.cluster_id == StoryCluster.id)
        .join(FeedArticle, FeedArticle.article_id == StoryClusterMember.article_id)
        .where(FeedArticle.feed_id == source_id)
        .group_by(StoryCluster.id)
        .order_by(recency.desc(), StoryCluster.id.desc())
    )
    if cursor:
        timestamp, item_id = cursor
        query = query.having(
            or_(recency < timestamp, and_(recency == timestamp, StoryCluster.id < item_id))
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    page = [row.StoryCluster for row in rows[:limit]]
    found = {
        row.cluster_id: row
        for row in await db.execute(positions(source_id, cluster_ids=[c.id for c in page]))
    }
    representatives = await load_representatives(db, [c.representative_article_id for c in page])
    next_cursor = (
        encode_cursor(rows[limit - 1].recency, rows[limit - 1].StoryCluster.id)
        if len(rows) > limit
        else None
    )
    items = []
    for cluster in page:
        mine = found[cluster.id]
        shared = cluster.source_count >= 2
        items.append(
            SourceClusterResponse(
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
                source_article_id=mine.article_id,
                first=mine.is_first if shared else None,
                minutes_behind=float(mine.minutes) if shared else None,
            )
        )
    return SourceClusterPage(items=items, next_cursor=next_cursor)


async def fetches(
    db: AsyncSession, source_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> FetchPage:
    query = (
        select(FeedFetch)
        .where(FeedFetch.feed_id == source_id)
        .order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc())
    )
    if cursor:
        started, item_id = cursor
        query = query.where(
            or_(
                FeedFetch.started_at < started,
                and_(FeedFetch.started_at == started, FeedFetch.id < item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = (
        encode_cursor(rows[limit - 1].started_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return FetchPage(
        items=[FetchResponse.model_validate(r) for r in rows[:limit]], next_cursor=next_cursor
    )
