import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, cast, exists, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.sqltypes import Date

from app.clustering.models import StoryCluster, StoryClusterMember
from app.entities.schemas import (
    EntityArticlePage,
    EntityClusterPage,
    EntityClusterResponse,
    EntityDossierResponse,
    EntityRelationshipsResponse,
    MentionTimelineDay,
    RelatedCountryResponse,
    RelatedEntityResponse,
    RelatedFeedResponse,
)
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import article_response, encode_cursor
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity


def window_start(days: int, now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return datetime.combine((current - timedelta(days=days - 1)).date(), datetime.min.time(), UTC)


async def get_entity(db: AsyncSession, entity_id: uuid.UUID) -> Entity | None:
    return await db.get(Entity, entity_id)


def _effective_date() -> ColumnElement[datetime]:
    return func.coalesce(Article.published_at, Article.first_discovered_at)


async def dossier(db: AsyncSession, entity: Entity, days: int) -> EntityDossierResponse:
    aggregate = (
        select(
            func.coalesce(func.sum(ArticleEntity.occurrence_count), 0),
            func.count(func.distinct(ArticleEntity.article_id)),
            func.count(func.distinct(StoryClusterMember.cluster_id)),
            func.min(Article.first_discovered_at),
            func.max(Article.first_discovered_at),
        )
        .select_from(ArticleEntity)
        .join(Article, Article.id == ArticleEntity.article_id)
        .outerjoin(StoryClusterMember, StoryClusterMember.article_id == ArticleEntity.article_id)
        .where(ArticleEntity.entity_id == entity.id, ArticleEntity.is_current.is_(True))
    )
    mentions, article_count, cluster_count, first_seen, last_seen = (
        await db.execute(aggregate)
    ).one()
    start = window_start(days)
    effective = _effective_date()
    utc_effective = func.timezone(literal_column("'UTC'"), effective)
    timeline_day = cast(func.date_trunc(literal_column("'day'"), utc_effective), Date)
    timeline_rows = (
        await db.execute(
            select(
                timeline_day.label("day"),
                func.coalesce(func.sum(ArticleEntity.occurrence_count), 0).label("mentions"),
            )
            .join(Article, Article.id == ArticleEntity.article_id)
            .where(
                ArticleEntity.entity_id == entity.id,
                ArticleEntity.is_current.is_(True),
                effective >= start,
            )
            .group_by(timeline_day)
        )
    ).all()
    timeline_counts = {row.day: int(row.mentions) for row in timeline_rows}
    first_day = start.date()
    timeline = [
        MentionTimelineDay(
            date=first_day + timedelta(days=offset),
            mentions=timeline_counts.get(first_day + timedelta(days=offset), 0),
        )
        for offset in range(days)
    ]
    return EntityDossierResponse(
        id=entity.id,
        display_name=entity.display_text,
        normalized_text=entity.normalized_text,
        language=entity.language,
        entity_type=entity.entity_type,
        total_mentions=int(mentions),
        article_count=int(article_count),
        cluster_count=int(cluster_count),
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        timeline_days=days,
        timeline=timeline,
    )


async def articles(
    db: AsyncSession, entity_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> EntityArticlePage:
    effective = _effective_date()
    query = (
        select(Article, effective.label("effective_date"))
        .where(
            exists(
                select(1).where(
                    ArticleEntity.article_id == Article.id,
                    ArticleEntity.entity_id == entity_id,
                    ArticleEntity.is_current.is_(True),
                )
            )
        )
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
    return EntityArticlePage(
        items=[article_response(row.Article) for row in rows[:limit]], next_cursor=next_cursor
    )


async def clusters(
    db: AsyncSession, entity_id: uuid.UUID, limit: int, cursor: tuple[datetime, uuid.UUID] | None
) -> EntityClusterPage:
    recency = func.coalesce(StoryCluster.last_published_at, StoryCluster.created_at)
    query = (
        select(StoryCluster, recency.label("recency"))
        .join(StoryClusterMember, StoryClusterMember.cluster_id == StoryCluster.id)
        .join(ArticleEntity, ArticleEntity.article_id == StoryClusterMember.article_id)
        .where(ArticleEntity.entity_id == entity_id, ArticleEntity.is_current.is_(True))
        .group_by(StoryCluster.id)
        .order_by(recency.desc(), StoryCluster.id.desc())
    )
    if cursor:
        timestamp, item_id = cursor
        query = query.having(
            or_(recency < timestamp, and_(recency == timestamp, StoryCluster.id < item_id))
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    cluster_ids = [
        row.StoryCluster.representative_article_id
        for row in rows[:limit]
        if row.StoryCluster.representative_article_id
    ]
    representatives: dict[uuid.UUID, Article] = {}
    if cluster_ids:
        representative_rows = await db.scalars(
            select(Article)
            .where(Article.id.in_(cluster_ids))
            .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        )
        representatives = {article.id: article for article in representative_rows}
    next_cursor = (
        encode_cursor(rows[limit - 1].recency, rows[limit - 1].StoryCluster.id)
        if len(rows) > limit
        else None
    )
    return EntityClusterPage(
        items=[
            EntityClusterResponse(
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
            )
            for cluster, _recency in rows[:limit]
        ],
        next_cursor=next_cursor,
    )


async def relationships(
    db: AsyncSession, entity_id: uuid.UUID, days: int
) -> EntityRelationshipsResponse:
    start = window_start(days)
    effective = _effective_date()
    scoped_articles = (
        select(ArticleEntity.article_id)
        .join(Article, Article.id == ArticleEntity.article_id)
        .where(
            ArticleEntity.entity_id == entity_id,
            ArticleEntity.is_current.is_(True),
            effective >= start,
        )
        .cte("scoped_articles")
    )
    entity_count = func.count(func.distinct(ArticleEntity.article_id)).label("article_count")
    entity_rows = (
        await db.execute(
            select(Entity, entity_count)
            .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
            .join(scoped_articles, scoped_articles.c.article_id == ArticleEntity.article_id)
            .where(ArticleEntity.is_current.is_(True), Entity.id != entity_id)
            .group_by(Entity.id)
            .order_by(entity_count.desc(), Entity.id.asc())
            .limit(10)
        )
    ).all()
    country_count = func.count(func.distinct(ArticleCountryAnnotation.article_id)).label(
        "article_count"
    )
    country_rows = (
        await db.execute(
            select(
                ArticleCountryAnnotation.country_code, ArticleCountryAnnotation.role, country_count
            )
            .join(
                scoped_articles, scoped_articles.c.article_id == ArticleCountryAnnotation.article_id
            )
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
    feed_count = func.count(func.distinct(FeedArticle.article_id)).label("article_count")
    feed_rows = (
        await db.execute(
            select(Feed, feed_count)
            .join(FeedArticle, FeedArticle.feed_id == Feed.id)
            .join(scoped_articles, scoped_articles.c.article_id == FeedArticle.article_id)
            .group_by(Feed.id)
            .order_by(feed_count.desc(), Feed.id.asc())
            .limit(10)
        )
    ).all()
    return EntityRelationshipsResponse(
        window_days=days,
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
        feeds=[
            RelatedFeedResponse(id=feed.id, name=feed.name, article_count=int(count))
            for feed, count in feed_rows
        ],
    )
