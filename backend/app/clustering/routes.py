import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    ClusterMemberFeedRef,
    ClusterMemberPage,
    ClusterMemberResponse,
    StoryClusterDetailResponse,
)
from app.clustering.service import request_clustering
from app.db.session import get_db
from app.feeds.models import Article, FeedArticle
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


@router.get("/clusters/{cluster_id}", response_model=StoryClusterDetailResponse)
async def get_cluster(
    cluster_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> StoryClusterDetailResponse:
    cluster = await db.get(StoryCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "Story cluster not found")
    effective_date = func.coalesce(Article.published_at, Article.first_discovered_at)
    query = (
        select(StoryClusterMember, Article, effective_date.label("effective_date"))
        .join(Article, Article.id == StoryClusterMember.article_id)
        .where(StoryClusterMember.cluster_id == cluster_id)
        .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        .order_by(effective_date.desc(), StoryClusterMember.article_id.desc())
    )
    if cursor:
        cursor_date, cursor_id = decode_cursor(cursor)
        query = query.where(
            or_(
                effective_date < cursor_date,
                and_(effective_date == cursor_date, StoryClusterMember.article_id < cursor_id),
            )
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    next_cursor = (
        encode_cursor(rows[limit - 1][2], rows[limit - 1][0].article_id)
        if len(rows) > limit
        else None
    )
    items = [
        ClusterMemberResponse(
            article_id=article.id,
            title=article.title,
            effective_date=row_effective_date,
            feeds=[
                ClusterMemberFeedRef(id=discovery.feed_id, name=discovery.feed.name)
                for discovery in article.discoveries
            ],
            score=member.score,
        )
        for member, article, row_effective_date in rows[:limit]
    ]
    return StoryClusterDetailResponse(
        id=cluster.id,
        algorithm_version=cluster.algorithm_version,
        article_count=cluster.article_count,
        source_count=cluster.source_count,
        first_published_at=cluster.first_published_at,
        last_published_at=cluster.last_published_at,
        representative_article_id=cluster.representative_article_id,
        members=ClusterMemberPage(items=items, next_cursor=next_cursor),
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
