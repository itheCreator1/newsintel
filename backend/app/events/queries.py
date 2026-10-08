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
from app.entities.resolver import names_of
from app.events.models import Event, EventCluster, EventEntity
from app.events.schemas import (
    EventArticlePage,
    EventArticleResponse,
    EventClusterPage,
    EventClusterResponse,
    EventDetail,
    EventEntityResponse,
    EventPage,
    EventSourceResponse,
    EventSummary,
    EventTimelineDay,
    EventTimelinePage,
    TimelineEvidence,
)
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import article_response, decode_cursor, encode_cursor
from app.nlp.models import Entity

LIST_ENTITIES = 5
DETAIL_ENTITIES = 20
DETAIL_SOURCES = 10
EVIDENCE_PER_DAY = 3

Cursor = tuple[datetime, uuid.UUID]
# The "biggest" order pages on (article count, end, id).
SizeCursor = tuple[int, datetime, uuid.UUID]


def encode_size_cursor(article_count: int, ended_at: datetime, item_id: uuid.UUID) -> str:
    return encode_cursor(ended_at, item_id) + f".{article_count}"


def decode_size_cursor(value: str) -> SizeCursor:
    """Raises ValueError (or UnicodeDecodeError) on anything `encode_size_cursor` did not make."""
    head, _, count = value.rpartition(".")
    ended_at, item_id = decode_cursor(head)
    return int(count), ended_at, item_id


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
            order_by=(EventEntity.article_count.desc(), Entity.name.asc(), Entity.id.asc()),
        )
        .label("rank")
    )
    ranked = (
        select(
            EventEntity.event_id,
            Entity.id,
            Entity.name,
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
    articles = func.count(func.distinct(FeedArticle.article_id))
    sources = await db.execute(
        select(Feed.id, Feed.name, articles)
        .select_from(EventCluster)
        .join(StoryClusterMember, StoryClusterMember.cluster_id == EventCluster.cluster_id)
        .join(FeedArticle, FeedArticle.article_id == StoryClusterMember.article_id)
        .join(Feed, Feed.id == FeedArticle.feed_id)
        .where(EventCluster.event_id == event.id)
        .group_by(Feed.id, Feed.name)
        .order_by(articles.desc(), Feed.name, Feed.id)
        .limit(DETAIL_SOURCES)
    )
    return EventDetail(
        **summary.model_dump(),
        created_at=event.created_at,
        updated_at=event.updated_at,
        sources=[
            EventSourceResponse(id=feed_id, name=name, article_count=count)
            for feed_id, name, count in sources
        ],
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
    min_stories: int = 1,
    sort: str = "latest",
    limit: int,
    cursor: Cursor | SizeCursor | None,
) -> EventPage:
    """`cursor` is a `SizeCursor` for the "biggest" order and a `Cursor` otherwise."""
    query = select(Event).where(Event.algorithm_version == version, Event.ended_at.is_not(None))
    if status:
        query = query.where(Event.status == status)
    if country:
        query = query.where(Event.primary_country == country.upper())
    if entity_id:
        query = query.where(
            exists(
                select(1).where(
                    EventEntity.event_id == Event.id,
                    # Any name of its root: events built before a merge still name the variant.
                    EventEntity.entity_id.in_(names_of([entity_id])),
                )
            )
        )
    if start:
        query = query.where(Event.ended_at >= start)
    if end:
        query = query.where(Event.started_at <= end)
    if min_stories > 1:
        query = query.where(Event.cluster_count >= min_stories)
    if sort == "biggest":
        if cursor:
            count, timestamp, item_id = cursor  # type: ignore[misc]
            query = query.where(
                or_(
                    Event.article_count < count,
                    and_(Event.article_count == count, Event.ended_at < timestamp),
                    and_(
                        Event.article_count == count,
                        Event.ended_at == timestamp,
                        Event.id < item_id,
                    ),
                )
            )
        query = query.order_by(Event.article_count.desc(), Event.ended_at.desc(), Event.id.desc())
    else:
        if cursor:
            timestamp, item_id = cursor  # type: ignore[misc]
            query = query.where(
                or_(
                    Event.ended_at < timestamp,
                    and_(Event.ended_at == timestamp, Event.id < item_id),
                )
            )
        query = query.order_by(Event.ended_at.desc(), Event.id.desc())
    rows = list(await db.scalars(query.limit(limit + 1)))
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page[-1].ended_at:
        last = page[-1]
        next_cursor = (
            encode_size_cursor(last.article_count, last.ended_at, last.id)  # type: ignore[arg-type]
            if sort == "biggest"
            else encode_cursor(last.ended_at, last.id)  # type: ignore[arg-type]
        )
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
