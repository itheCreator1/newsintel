import uuid
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
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
from app.nlp.models import Entity
from app.search.aggregations import date_histogram, ensure_complete, read_terms, terms
from app.search.criteria import SearchCriteria, build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

router = APIRouter(tags=["analytics"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Criteria = Annotated[SearchCriteria, Depends(search_criteria)]

TOP_N = 10
Scope = Literal["recent", "investigation"]
# Spare entity buckets: one the catalogue no longer holds is skipped and the next takes its
# place, as the SQL join did. ponytail: more than TOP_N misses in one ranking still comes back
# short, fetch more candidates or reindex if merges ever outpace indexing.
ENTITY_CANDIDATES = TOP_N * 2


def _since(scope: Scope) -> datetime | None:
    """Recent keeps Overview's rolling window; investigation is the criteria alone."""
    return None if scope == "investigation" else datetime.now(UTC) - timedelta(days=DISPLAY_DAYS)


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


# ponytail: counts indexed articles, so they lag delivery and skip documents that never indexed;
# read PostgreSQL again if ingestion monitoring needs read-after-write counts.
@router.get("/analytics/ingestion-timeline", response_model=IngestionTimelineResponse)
async def ingestion_timeline(
    db: Db, _auth: Auth, settings: Config, criteria: Criteria
) -> IngestionTimelineResponse:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=LOOKBACK_DAYS - 1)
    cutoff = datetime.combine(start, time.min, UTC)
    # A recent-ingestion metric, so no scope: the criteria can narrow it, never widen the window.
    # Drop start/end: they filter effective_date (publication), which would starve the trailing
    # baseline detect_spikes needs and manufacture spikes out of the emptied days.
    aggregations = await _aggregate(
        db,
        settings,
        replace(criteria, start=None, end=None),
        {
            "days": date_histogram(
                "first_discovered_at", "day", cutoff, datetime.combine(today, time.min, UTC)
            )
        },
        minimum=2,
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
    db: Db, _auth: Auth, settings: Config, criteria: Criteria, scope: Scope = "recent"
) -> TopEntitiesResponse:
    # entity_type filters the articles (criteria) and the buckets: an article matching PERSON
    # still carries ORG entities, which must not be ranked.
    within = {"terms": {"entities.type": criteria.entity_types}} if criteria.entity_types else None
    aggregations = await _aggregate(
        db,
        settings,
        criteria,
        {"entities": terms("entities", "entities.id", ENTITY_CANDIDATES, within=within)},
        minimum=2,
        since=_since(scope),
    )
    ranked = read_terms(aggregations.get("entities", {}), nested=True).buckets
    rows = (
        await db.execute(
            select(Entity.id, Entity.display_text, Entity.entity_type).where(
                Entity.id.in_([uuid.UUID(value) for value, _count in ranked])
            )
        )
    ).all()
    catalogue = {str(row.id): row for row in rows}
    return TopEntitiesResponse(
        entities=[
            TopEntity(
                entity_id=row.id,
                display_text=row.display_text,
                entity_type=row.entity_type,
                count=count,
            )
            for value, count in ranked
            if (row := catalogue.get(value)) is not None
        ][:TOP_N]
    )


@router.get("/analytics/top-countries", response_model=TopCountriesResponse)
async def top_countries(
    db: Db, _auth: Auth, settings: Config, criteria: Criteria, scope: Scope = "recent"
) -> TopCountriesResponse:
    aggregations = await _aggregate(
        db,
        settings,
        criteria,
        {"countries": terms(None, "primary_story_country", TOP_N)},
        minimum=2,
        since=_since(scope),
    )
    ranked = read_terms(aggregations.get("countries", {}), nested=False).buckets
    return TopCountriesResponse(
        countries=[TopCountry(country_code=value, count=count) for value, count in ranked]
    )
