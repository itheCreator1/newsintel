import uuid
from datetime import datetime

from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clustering.models import StoryClusterMember
from app.compare.queries import ANNOTATION_ROLE, subject_articles
from app.entities.queries import effective_date, window_start
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import article_response, encode_cursor
from app.geo.schemas import (
    ArticleRole,
    GeoArticlePage,
    GeoCountriesResponse,
    GeoCountry,
    GeoCoverage,
    Role,
)
from app.nlp.models import ArticleCountryAnnotation


def _located(role: ArticleRole, start: datetime) -> Select:  # type: ignore[type-arg]
    """`(article_id, code, feed_id)` for every article in the window that has a country in `role`.

    The same predicates as `subject_articles`, so a country's count is its evidence set's size.
    """
    effective = effective_date()
    if role == "source":
        code = func.upper(Feed.source_country)
        return (
            select(Article.id.label("article_id"), code.label("code"), Feed.id.label("feed_id"))
            .join(FeedArticle, FeedArticle.article_id == Article.id)
            .join(Feed, Feed.id == FeedArticle.feed_id)
            .where(Feed.source_country.is_not(None), effective >= start)
            .distinct()
        )
    return (
        select(
            Article.id.label("article_id"),
            ArticleCountryAnnotation.country_code.label("code"),
            literal(None).label("feed_id"),
        )
        .join(ArticleCountryAnnotation, ArticleCountryAnnotation.article_id == Article.id)
        .where(
            ArticleCountryAnnotation.role == ANNOTATION_ROLE[role],
            ArticleCountryAnnotation.is_current.is_(True),
            effective >= start,
        )
        .distinct()
    )


async def _article_countries(
    db: AsyncSession, role: ArticleRole, start: datetime
) -> tuple[GeoCoverage, list[GeoCountry]]:
    located = _located(role, start).cte("located")
    grouped = (
        select(
            located.c.code,
            func.count(func.distinct(located.c.article_id)).label("articles"),
            func.count(func.distinct(StoryClusterMember.cluster_id)).label("stories"),
            func.count(func.distinct(located.c.feed_id)).label("sources"),
        )
        .join(
            StoryClusterMember, StoryClusterMember.article_id == located.c.article_id, isouter=True
        )
        .group_by(located.c.code)
        .order_by(func.count(func.distinct(located.c.article_id)).desc(), located.c.code)
    )
    counts = select(
        select(func.count())
        .select_from(Article)
        .where(effective_date() >= start)
        .scalar_subquery(),
        select(func.count(func.distinct(located.c.article_id))).scalar_subquery(),
    )
    rows = (await db.execute(grouped)).all()
    total, found = (await db.execute(counts)).one()
    return (
        GeoCoverage(unit="articles", window_total=total, located=found),
        [
            GeoCountry(
                country_code=row.code,
                articles=row.articles,
                stories=row.stories,
                sources=row.sources if role == "source" else None,
                events=None,
            )
            for row in rows
        ],
    )


async def _event_countries(
    db: AsyncSession, start: datetime
) -> tuple[GeoCoverage, list[GeoCountry]]:
    """Events of the current algorithm version whose span reaches into the window."""
    code = Event.primary_country  # as stored, so a count matches `/events?country=` exactly
    n = func.count()
    rows = (
        await db.execute(
            select(code.label("code"), n.label("n"))
            .where(
                Event.algorithm_version == EVENT_ALGORITHM_VERSION,
                Event.ended_at.is_not(None),
                Event.ended_at >= start,
            )
            .group_by(code)
            .order_by(n.desc(), code)
        )
    ).all()
    located = sum(row.n for row in rows if row.code is not None)
    return (
        GeoCoverage(unit="events", window_total=sum(row.n for row in rows), located=located),
        [
            GeoCountry(
                country_code=row.code, articles=None, stories=None, sources=None, events=row.n
            )
            for row in rows
            if row.code is not None
        ],
    )


async def countries(db: AsyncSession, role: Role, days: int) -> GeoCountriesResponse:
    start = window_start(days)
    if role == "event":
        coverage, items = await _event_countries(db, start)
    else:
        coverage, items = await _article_countries(db, role, start)
    return GeoCountriesResponse(
        role=role, days=days, window_start=start, coverage=coverage, items=items
    )


async def articles(
    db: AsyncSession,
    role: ArticleRole,
    code: str,
    days: int,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None,
) -> GeoArticlePage:
    effective = effective_date()
    found = subject_articles("country", code, role, window_start(days))
    query = (
        select(Article, effective.label("effective_date"))
        .where(Article.id.in_(select(found.subquery().c.article_id)))
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
    return GeoArticlePage(
        items=[article_response(row.Article) for row in rows[:limit]], next_cursor=next_cursor
    )
