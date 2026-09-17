from datetime import UTC, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.schemas import (
    IngestionTimelineBucket,
    IngestionTimelineResponse,
    TopCountriesResponse,
    TopCountry,
    TopEntitiesResponse,
    TopEntity,
)
from app.analytics.timeline import (
    DISPLAY_DAYS,
    LOOKBACK_DAYS,
    TRAILING_WINDOW,
    daily_counts,
    detect_spikes,
)
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.feeds.models import Article
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity

router = APIRouter(tags=["analytics"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]

TOP_N = 10


@router.get("/analytics/ingestion-timeline", response_model=IngestionTimelineResponse)
async def ingestion_timeline(db: Db, _auth: Auth) -> IngestionTimelineResponse:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=LOOKBACK_DAYS - 1)
    cutoff = datetime.combine(start, time.min, UTC)
    day = func.date_trunc("day", func.timezone("UTC", Article.first_discovered_at))
    rows = (
        await db.execute(
            select(day.label("day"), func.count())
            .where(Article.first_discovered_at >= cutoff)
            .group_by("day")
        )
    ).all()
    counts_by_day = {row.day.date(): row[1] for row in rows}
    counts = daily_counts(counts_by_day, start, LOOKBACK_DAYS)
    spikes = detect_spikes(counts)
    buckets = [
        IngestionTimelineBucket(
            date=start + timedelta(days=TRAILING_WINDOW + offset),
            count=count,
            is_spike=spike,
        )
        for offset, (count, spike) in enumerate(
            zip(counts[TRAILING_WINDOW:], spikes[TRAILING_WINDOW:], strict=True)
        )
    ]
    return IngestionTimelineResponse(buckets=buckets)


@router.get("/analytics/top-entities", response_model=TopEntitiesResponse)
async def top_entities(
    db: Db, _auth: Auth, entity_type: str | None = Query(None)
) -> TopEntitiesResponse:
    cutoff = datetime.now(UTC) - timedelta(days=DISPLAY_DAYS)
    article_count = func.count(func.distinct(ArticleEntity.article_id))
    query = (
        select(
            Entity.id, Entity.display_text, Entity.entity_type, article_count.label("article_count")
        )
        .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
        .join(Article, Article.id == ArticleEntity.article_id)
        .where(ArticleEntity.is_current.is_(True), Article.first_discovered_at >= cutoff)
        .group_by(Entity.id, Entity.display_text, Entity.entity_type)
        .order_by(article_count.desc(), Entity.id)
        .limit(TOP_N)
    )
    if entity_type:
        query = query.where(Entity.entity_type == entity_type.upper())
    rows = (await db.execute(query)).all()
    return TopEntitiesResponse(
        entities=[
            TopEntity(
                entity_id=row.id,
                display_text=row.display_text,
                entity_type=row.entity_type,
                count=row.article_count,
            )
            for row in rows
        ]
    )


@router.get("/analytics/top-countries", response_model=TopCountriesResponse)
async def top_countries(db: Db, _auth: Auth) -> TopCountriesResponse:
    cutoff = datetime.now(UTC) - timedelta(days=DISPLAY_DAYS)
    article_count = func.count(func.distinct(ArticleCountryAnnotation.article_id))
    query = (
        select(ArticleCountryAnnotation.country_code, article_count.label("article_count"))
        .join(Article, Article.id == ArticleCountryAnnotation.article_id)
        .where(
            ArticleCountryAnnotation.role == "primary",
            ArticleCountryAnnotation.is_current.is_(True),
            Article.first_discovered_at >= cutoff,
        )
        .group_by(ArticleCountryAnnotation.country_code)
        .order_by(article_count.desc(), ArticleCountryAnnotation.country_code)
        .limit(TOP_N)
    )
    rows = (await db.execute(query)).all()
    return TopCountriesResponse(
        countries=[
            TopCountry(country_code=row.country_code, count=row.article_count) for row in rows
        ]
    )
