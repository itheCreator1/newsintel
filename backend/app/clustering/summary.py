"""The story page's header, read from Postgres: title, entities, event, timeline, first reports."""

from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.articles.models import Article
from app.clustering.models import StoryCluster, StoryClusterMember
from app.clustering.schemas import (
    StoryEntity,
    StoryEventRef,
    StorySource,
    StorySummary,
    StoryTimeline,
    StoryTimelineBucket,
)
from app.entities.models import Entity
from app.entities.queries import effective_date
from app.events import queries as event_queries
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event, EventCluster
from app.feeds.models import Feed, FeedArticle
from app.nlp.models import ArticleEntity

STORY_ENTITIES = 12
STORY_SOURCES = 100
# A story spanning up to a week is charted by hour (at most 169 buckets), a longer one by day.
HOURLY_SPAN = timedelta(days=7)


def _timeline(dates: list[datetime]) -> StoryTimeline:
    if not dates:
        return StoryTimeline(interval="hour", buckets=[])
    hourly = max(dates) - min(dates) <= HOURLY_SPAN
    step = timedelta(hours=1) if hourly else timedelta(days=1)

    def floor(value: datetime) -> datetime:
        value = value.replace(minute=0, second=0, microsecond=0)
        return value if hourly else value.replace(hour=0)

    counts: dict[datetime, int] = {}
    for value in dates:
        counts[floor(value)] = counts.get(floor(value), 0) + 1
    buckets, cursor, last = [], min(counts), max(counts)
    # Quiet buckets are kept, so the chart's time axis is not squeezed.
    while cursor <= last:
        buckets.append(StoryTimelineBucket(start=cursor, count=counts.get(cursor, 0)))
        cursor += step
    return StoryTimeline(interval="hour" if hourly else "day", buckets=buckets)


async def story_summary(db: AsyncSession, cluster: StoryCluster) -> StorySummary:
    effective = effective_date()
    members = select(StoryClusterMember.article_id).where(
        StoryClusterMember.cluster_id == cluster.id
    )
    title = None
    if cluster.representative_article_id is not None:
        title = await db.scalar(
            select(Article.title).where(Article.id == cluster.representative_article_id)
        )
    if title is None:
        title = await db.scalar(
            select(Article.title)
            .where(Article.id.in_(members))
            .order_by(effective, Article.id)
            .limit(1)
        )

    articles = func.count(distinct(ArticleEntity.article_id))
    entity_rows = await db.execute(
        select(Entity.id, Entity.name, Entity.kind, articles)
        .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
        .where(
            ArticleEntity.article_id.in_(members),
            ArticleEntity.is_current.is_(True),
            Entity.hidden.is_(False),
        )
        .group_by(Entity.id)
        .order_by(articles.desc(), Entity.name, Entity.id)
        .limit(STORY_ENTITIES)
    )

    event = await db.scalar(
        select(Event)
        .join(EventCluster, EventCluster.event_id == Event.id)
        .where(
            EventCluster.cluster_id == cluster.id,
            EventCluster.algorithm_version == EVENT_ALGORITHM_VERSION,
        )
    )
    event_ref = None
    if event is not None:
        (summary,) = await event_queries.summaries(db, [event], 0)
        event_ref = StoryEventRef(id=event.id, headline=summary.headline)

    dates = list((await db.scalars(select(effective).where(Article.id.in_(members)))).all())
    first = func.min(effective)
    source_rows = await db.execute(
        select(Feed.id, Feed.name, first)
        .join(FeedArticle, FeedArticle.feed_id == Feed.id)
        .join(Article, Article.id == FeedArticle.article_id)
        .where(Article.id.in_(members))
        .group_by(Feed.id, Feed.name)
        .order_by(first, Feed.name, Feed.id)
        .limit(STORY_SOURCES)
    )
    start = min(dates) if dates else None
    return StorySummary(
        id=cluster.id,
        title=title,
        entities=[
            StoryEntity(id=item_id, display_name=name, entity_type=kind, article_count=count)
            for item_id, name, kind, count in entity_rows
        ],
        event=event_ref,
        timeline=_timeline(dates),
        sources=[
            StorySource(
                id=item_id,
                name=name,
                first_reported_at=at,
                minutes_behind=(at - start).total_seconds() / 60 if start else 0.0,
            )
            for item_id, name, at in source_rows
        ],
    )
