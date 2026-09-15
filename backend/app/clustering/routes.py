import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.clustering.engine import CLUSTER_ALGORITHM_VERSION
from app.clustering.models import ClusterJob, StoryCluster, StoryClusterMember
from app.clustering.schemas import (
    ClusteringFailurePage,
    ClusteringFailureResponse,
    ClusteringMutationResponse,
    ClusteringStatusResponse,
)
from app.clustering.service import request_clustering
from app.db.session import get_db
from app.feeds.service import decode_cursor, encode_cursor

router = APIRouter(tags=["clustering"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


@router.get("/clustering/status", response_model=ClusteringStatusResponse)
async def clustering_status(db: Db, _auth: Auth) -> ClusteringStatusResponse:
    counts = {
        str(name): int(count)
        for name, count in (
            await db.execute(select(ClusterJob.status, func.count()).group_by(ClusterJob.status))
        ).all()
    }
    return ClusteringStatusResponse(
        **{name: counts.get(name, 0) for name in ("queued", "running", "retrying", "failed")},
        algorithm_version=CLUSTER_ALGORITHM_VERSION,
        clusters=int(await db.scalar(select(func.count()).select_from(StoryCluster)) or 0),
        clustered_articles=int(
            await db.scalar(select(func.count()).select_from(StoryClusterMember)) or 0
        ),
    )


@router.get("/clustering/failures", response_model=ClusteringFailurePage)
async def clustering_failures(
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ClusteringFailurePage:
    query = (
        select(ClusterJob)
        .where(ClusterJob.status == "failed")
        .order_by(ClusterJob.created_at.desc(), ClusterJob.id.desc())
    )
    if cursor:
        created, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                ClusterJob.created_at < created,
                and_(ClusterJob.created_at == created, ClusterJob.id < item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = (
        encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return ClusteringFailurePage(
        items=[
            ClusteringFailureResponse(
                id=row.id,
                article_id=row.article_id,
                algorithm_version=row.algorithm_version,
                attempt_count=row.attempt_count,
                error_category=row.error_category,
                error_message=row.error_message,
                created_at=row.created_at,
            )
            for row in rows[:limit]
        ],
        next_cursor=next_cursor,
    )


@router.post(
    "/clustering/jobs/{job_id}/retry", response_model=ClusteringMutationResponse, status_code=202
)
async def retry_clustering_job(
    job_id: uuid.UUID, db: Db, _mutation: Mutation
) -> ClusteringMutationResponse:
    job = await db.get(ClusterJob, job_id)
    if job is None or job.status != "failed":
        raise HTTPException(404, "Failed clustering job not found")
    count = await request_clustering(db, job.article_id)
    await db.commit()
    return ClusteringMutationResponse(status="queued", jobs_created=count)
