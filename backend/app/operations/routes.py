import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.operations import probes, queries
from app.operations.schemas import (
    Area,
    ElasticsearchStorage,
    FailuresResponse,
    FeedsResponse,
    HealthResponse,
    PipelinesResponse,
    StorageResponse,
)
from app.search.elasticsearch import ElasticsearchAdapter
from app.search.rebuild import ALIAS

router = APIRouter(tags=["operations"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Hours = Annotated[int, Query(ge=1, le=168, description="Window length in hours, ending now.")]


async def _bounded[T](awaitable: Awaitable[T]) -> T:
    return await asyncio.wait_for(awaitable, probes.PROBE_TIMEOUT)


@router.get("/operations/health", response_model=HealthResponse)
async def operations_health(db: Db, _auth: Auth, settings: Config) -> HealthResponse:
    """Dependency probes under a hard timeout each. An unreachable dependency is reported as data
    (HTTP 200), so one outage never hides the rest."""
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=probes.PROBE_TIMEOUT)
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    try:
        found = await probes.run_all(
            [
                ("postgres", probes.postgres_check(db)),
                ("redis", probes.redis_check(redis)),
                ("elasticsearch", probes.elasticsearch_check(adapter)),
                ("nlp", probes.nlp_check(settings)),
                ("scheduler", probes.scheduler_check(redis)),
            ]
        )
        try:
            depths = await _bounded(probes.queue_depths(redis))
        except Exception:
            depths = []  # Redis is down: its probe already says so
    finally:
        await redis.aclose()
    return HealthResponse(generated_at=datetime.now(UTC), probes=found, queues=depths)


@router.get("/operations/pipelines", response_model=PipelinesResponse)
async def operations_pipelines(db: Db, _auth: Auth, hours: Hours = 24) -> PipelinesResponse:
    return await queries.pipelines(db, datetime.now(UTC), hours)


@router.get("/operations/feeds", response_model=FeedsResponse)
async def operations_feeds(db: Db, _auth: Auth, hours: Hours = 24) -> FeedsResponse:
    return await queries.feeds(db, datetime.now(UTC), hours)


@router.get("/operations/storage", response_model=StorageResponse)
async def operations_storage(db: Db, _auth: Auth, settings: Config) -> StorageResponse:
    result = await queries.storage(db)
    try:
        documents, size = await _bounded(
            ElasticsearchAdapter(settings.elasticsearch_url).index_stats(ALIAS)
        )
        result.elasticsearch = ElasticsearchStorage(
            index=ALIAS, documents=documents, store_bytes=size
        )
    except Exception as exc:  # unreachable or no index yet: the class name only, as for probes
        result.elasticsearch_error = type(exc).__name__
    return result


@router.get("/operations/failures", response_model=FailuresResponse)
async def operations_failures(
    db: Db,
    _auth: Auth,
    area: Area,
    hours: Hours = 24,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> FailuresResponse:
    return await queries.failures(db, datetime.now(UTC), area, hours, limit)
