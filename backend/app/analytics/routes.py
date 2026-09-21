from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.feeds.models import Article
from app.nlp.models import ArticleCountryAnnotation, ArticleEntity, Entity
from app.search.aggregations import date_histogram, ensure_complete
from app.search.criteria import SearchCriteria, build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

router = APIRouter(tags=["analytics"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Criteria = Annotated[SearchCriteria, Depends(search_criteria)]

TOP_N = 10


async def _aggregate(
    db: AsyncSession,
    settings: Settings,
    criteria: SearchCriteria,
    aggs: dict[str, Any],
    *,
    minimum: int,
    since: datetime | None,
) -> dict[str, Any]:
    """`aggs` over the articles matching `criteria` first discovered since `since`."""
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    try:
        index_name, schema_version = await current_search_target(db, criteria, minimum=minimum)
        query = build_query(criteria, schema_version)
        if since is not None:
            # "Recent" is discovery; the criteria's own dates filter publication (effective_date).
            query["bool"]["filter"].append(
                {"range": {"first_discovered_at": {"gte": since.isoformat()}}}
            )
        response = await adapter.search_index(
            index_name, {"size": 0, "track_total_hits": False, "query": query, "aggs": aggs}
        )
        ensure_complete(response)
    except ElasticsearchUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Analytics are unavailable"
        ) from exc
    aggregations: dict[str, Any] = response.get("aggregations", {})
    return aggregations


@router.get("/analytics/ingestion-timeline", response_model=IngestionTimelineResponse)
async def ingestion_timeline(
    db: Db, _auth: Auth, settings: Config, criteria: Criteria
) -> IngestionTimelineResponse:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=LOOKBACK_DAYS - 1)
    cutoff = datetime.combine(start, time.min, UTC)
    # A recent-ingestion metric, so no scope: the criteria can narrow it, never widen the window.
    aggregations = await _aggregate(
        db,
        settings,
        criteria,
        {
            "days": date_histogram(
                "first_discovered_at", "day", cutoff, datetime.combine(today, time.min, UTC)
            )
        },
        minimum=1,
        since=cutoff,
    )
    counts_by_day = {
        datetime.fromtimestamp(bucket["key"] / 1000, UTC).date(): int(bucket["doc_count"])
        for bucket in aggregations.get("days", {}).get("buckets", [])
    }
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
