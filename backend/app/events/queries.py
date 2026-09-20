import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import and_, cast, exists, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.sqltypes import Date

from app.clustering.models import StoryCluster, StoryClusterMember
from app.entities.queries import effective_date, load_representatives
from app.events.models import Event, EventCluster, EventEntity
from app.events.schemas import (
    EventArticlePage,
    EventArticleResponse,
    EventClusterPage,
    EventClusterResponse,
    EventDetail,
    EventEntityResponse,
    EventPage,
    EventSummary,
    EventTimelineDay,
    EventTimelinePage,
    TimelineEvidence,
)
from app.feeds.models import Article, FeedArticle
from app.feeds.service import article_response, encode_cursor
from app.nlp.models import Entity

LIST_ENTITIES = 5
DETAIL_ENTITIES = 20
EVIDENCE_PER_DAY = 3

Cursor = tuple[datetime, uuid.UUID]


async def get_event(db: AsyncSession, event_id: uuid.UUID) -> Event | None:
    return await db.get(Event, event_id)


async def _counts(
    db: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, int]]:
    rows = await db.execute(
        select(
            EventCluster.event_id,
            func.count(func.distinct(EventCluster.cluster_id)),
            func.count(func.distinct(StoryClusterMember.article_id)),
            func.count(func.distinct(FeedArticle.feed_id)),
        )
        .select_from(EventCluster)
        .outerjoin(StoryClusterMember, StoryClusterMember.cluster_id == EventCluster.cluster_id)
        .outerjoin(FeedArticle, FeedArticle.article_id == StoryClusterMember.article_id)
        .where(EventCluster.event_id.in_(ids))
        .group_by(EventCluster.event_id)
    )
    return {event_id: (c, a, s) for event_id, c, a, s in rows}


async def _headlines(
    db: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    rows = await db.execute(
        select(EventCluster.event_id, Article.id, Article.title)
        .select_from(EventCluster)
        .join(StoryCluster, StoryCluster.id == EventCluster.cluster_id)
        .join(Article, Article.id == StoryCluster.representative_article_id)
        .where(EventCluster.event_id.in_(ids))
        .distinct(EventCluster.event_id)
        .order_by(
            EventCluster.event_id,
            StoryCluster.article_count.desc(),
            StoryCluster.first_published_at.asc().nulls_last(),
            StoryCluster.id.asc(),
        )
    )
    return {event_id: (article_id, title) for event_id, article_id, title in rows}


async def _top_entities(
    db: AsyncSession, ids: Sequence[uuid.UUID], limit: int
) -> dict[uuid.UUID, list[EventEntityResponse]]:
    rank = (
        func.row_number()
        .over(
            partition_by=EventEntity.event_id,
            order_by=(EventEntity.article_count.desc(), Entity.display_text.asc(), Entity.id.asc()),
        )
        .label("rank")
    )
    ranked = (
        select(
            EventEntity.event_id,
            Entity.id,
            Entity.display_text,
            Entity.entity_type,
            EventEntity.article_count,
            rank,
        )
        .join(Entity, Entity.id == EventEntity.entity_id)
        .where(EventEntity.event_id.in_(ids))
        .subquery()
    )
    rows = await db.execute(
        select(ranked).where(ranked.c.rank <= limit).order_by(ranked.c.event_id, ranked.c.rank)
    )
    found: dict[uuid.UUID, list[EventEntityResponse]] = defaultdict(list)
    for event_id, entity_id, name, kind, count, _rank in rows:
        found[event_id].append(
            EventEntityResponse(
                id=entity_id, display_name=name, entity_type=kind, article_count=count
            )
        )
    return found


async def summaries(
    db: AsyncSession, events: Sequence[Event], entity_limit: int
) -> list[EventSummary]:
    """Every derived value for a page of events comes from one query each, not one per event."""
    if not events:
        return []
    ids = [event.id for event in events]
    counts = await _counts(db, ids)
    headlines = await _headlines(db, ids)
    top = await _top_entities(db, ids, entity_limit)
    return [
        EventSummary(
            id=event.id,
            algorithm_version=event.algorithm_version,
            status=event.status,
            started_at=event.started_at,
            ended_at=event.ended_at,
            primary_country=event.primary_country,
            cluster_count=counts.get(event.id, (0, 0, 0))[0],
            article_count=counts.get(event.id, (0, 0, 0))[1],
            source_count=counts.get(event.id, (0, 0, 0))[2],
            headline=headlines[event.id][1] if event.id in headlines else None,
            headline_article_id=headlines[event.id][0] if event.id in headlines else None,
            entities=top.get(event.id, []),
        )
        for event in events
    ]


async def detail(db: AsyncSession, event: Event) -> EventDetail:
    (summary,) = await summaries(db, [event], DETAIL_ENTITIES)
    return EventDetail(
        **summary.model_dump(), created_at=event.created_at, updated_at=event.updated_at
    )


async def events(
    db: AsyncSession,
    *,
    version: str,
    status: str | None,
    country: str | None,
    entity_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    cursor: Cursor | None,
) -> EventPage:
    query = select(Event).where(Event.algorithm_version == version, Event.ended_at.is_not(None))
    if status:
        query = query.where(Event.status == status)
    if country:
        query = query.where(Event.primary_country == country.upper())
    if entity_id:
        query = query.where(
            exists(
                select(1).where(
                    EventEntity.event_id == Event.id, EventEntity.entity_id == entity_id
                )
            )
        )
    if start:
        query = query.where(Event.ended_at >= start)
    if end:
        query = query.where(Event.started_at <= end)
    if cursor:
        timestamp, item_id = cursor
        query = query.where(
            or_(Event.ended_at < timestamp, and_(Event.ended_at == timestamp, Event.id < item_id))
        )
    rows = list(
        await db.scalars(query.order_by(Event.ended_at.desc(), Event.id.desc()).limit(limit + 1))
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page[-1].ended_at:
        next_cursor = encode_cursor(page[-1].ended_at, page[-1].id)
    return EventPage(items=await summaries(db, page, LIST_ENTITIES), next_cursor=next_cursor)


async def clusters(
    db: AsyncSession, event_id: uuid.UUID, limit: int, cursor: Cursor | None
) -> EventClusterPage:
    recency = func.coalesce(StoryCluster.last_published_at, StoryCluster.created_at)
    query = (
        select(StoryCluster, EventCluster, recency.label("recency"))
        .join(EventCluster, EventCluster.cluster_id == StoryCluster.id)
        .where(EventCluster.event_id == event_id)
        .order_by(recency.desc(), StoryCluster.id.desc())
    )
    if cursor:
        timestamp, item_id = cursor
        query = query.where(
            or_(recency < timestamp, and_(recency == timestamp, StoryCluster.id < item_id))
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    page = rows[:limit]
    representatives = await load_representatives(
        db, [row.StoryCluster.representative_article_id for row in page]
    )
    next_cursor = (
        encode_cursor(page[-1].recency, page[-1].StoryCluster.id) if len(rows) > limit else None
    )
    return EventClusterPage(
        items=[
            EventClusterResponse(
                id=row.StoryCluster.id,
                article_count=row.StoryCluster.article_count,
                source_count=row.StoryCluster.source_count,
                first_published_at=row.StoryCluster.first_published_at,
                last_published_at=row.StoryCluster.last_published_at,
                representative_article=(
                    article_response(representatives[row.StoryCluster.representative_article_id])
                    if row.StoryCluster.representative_article_id in representatives
                    else None
                ),
                score=row.EventCluster.score,
                signals=row.EventCluster.signals,
                joined_at=row.EventCluster.joined_at,
            )
            for row in page
        ],
        next_cursor=next_cursor,
    )


async def articles(
    db: AsyncSession, event_id: uuid.UUID, limit: int, cursor: Cursor | None
) -> EventArticlePage:
    effective = effective_date()
    query = (
        select(Article, effective.label("effective_date"), StoryClusterMember.cluster_id)
        .join(StoryClusterMember, StoryClusterMember.article_id == Article.id)
        .join(EventCluster, EventCluster.cluster_id == StoryClusterMember.cluster_id)
        .where(EventCluster.event_id == event_id)
        .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        .order_by(effective.desc(), Article.id.desc())
    )
    if cursor:
        timestamp, item_id = cursor
        query = query.where(
            or_(effective < timestamp, and_(effective == timestamp, Article.id < item_id))
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].effective_date, page[-1].Article.id) if len(rows) > limit else None
    )
    return EventArticlePage(
        items=[
            EventArticleResponse(
                **article_response(row.Article).model_dump(), cluster_id=row.cluster_id
            )
            for row in page
        ],
        next_cursor=next_cursor,
    )


def _utc_day(moment: ColumnElement[datetime]) -> ColumnElement[date]:
    return cast(
        func.date_trunc(literal_column("'day'"), func.timezone(literal_column("'UTC'"), moment)),
        Date,
    )


async def timeline(
    db: AsyncSession, event_id: uuid.UUID, limit: int, after: date | None
) -> EventTimelinePage:
    """Ascending UTC-day buckets of the event's articles, paged by the last day already seen."""
    effective = effective_date()
    day = _utc_day(effective)
    members = (
        select(Article.id.label("article_id"), effective.label("at"), day.label("day"))
        .join(StoryClusterMember, StoryClusterMember.article_id == Article.id)
        .join(EventCluster, EventCluster.cluster_id == StoryClusterMember.cluster_id)
        .where(EventCluster.event_id == event_id)
        .cte("members")
    )
    days_query = (
        select(
            members.c.day,
            func.count(func.distinct(members.c.article_id)),
            func.count(func.distinct(FeedArticle.feed_id)),
        )
        .outerjoin(FeedArticle, FeedArticle.article_id == members.c.article_id)
        .group_by(members.c.day)
        .order_by(members.c.day)
        .limit(limit + 1)
    )
    if after:
        days_query = days_query.where(members.c.day > after)
    rows = (await db.execute(days_query)).all()
    page = rows[:limit]
    if not page:
        return EventTimelinePage(items=[], next_cursor=None)
    first, last = page[0][0], page[-1][0]

    started_day = _utc_day(func.coalesce(StoryCluster.first_published_at, StoryCluster.created_at))
    started_rows = await db.execute(
        select(started_day, func.count())
        .select_from(StoryCluster)
        .join(EventCluster, EventCluster.cluster_id == StoryCluster.id)
        .where(EventCluster.event_id == event_id, started_day.between(first, last))
        .group_by(started_day)
    )
    started = {item_day: count for item_day, count in started_rows}
    rank = (
        func.row_number()
        .over(partition_by=members.c.day, order_by=(members.c.at.asc(), members.c.article_id.asc()))
        .label("rank")
    )
    ranked = (
        select(members.c.day, members.c.article_id, Article.title, rank)
        .join(Article, Article.id == members.c.article_id)
        .where(members.c.day.between(first, last))
        .subquery()
    )
    evidence: dict[date, list[TimelineEvidence]] = defaultdict(list)
    for item_day, article_id, title, _rank in await db.execute(
        select(ranked)
        .where(ranked.c.rank <= EVIDENCE_PER_DAY)
        .order_by(ranked.c.day, ranked.c.rank)
    ):
        evidence[item_day].append(TimelineEvidence(article_id=article_id, title=title))
    return EventTimelinePage(
        items=[
            EventTimelineDay(
                date=item_day,
                article_count=articles_,
                source_count=sources,
                clusters_started=started.get(item_day, 0),
                evidence=evidence[item_day],
            )
            for item_day, articles_, sources in page
        ],
        next_cursor=page[-1][0] if len(rows) > limit else None,
    )
