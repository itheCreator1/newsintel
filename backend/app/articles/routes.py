import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.articles.schemas import (
    BacklogResponse,
    JobPage,
    JobResponse,
    ProcessRequest,
    ProcessResponse,
)
from app.articles.service import request_processing, retry_processing
from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.feeds.models import Article, ArticleProcessingJob
from app.feeds.service import decode_cursor, encode_cursor

router = APIRouter(tags=["article processing"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


def _response(job: ArticleProcessingJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        article_id=job.article_id,
        article_title=job.article.title,
        requested_mode=job.requested_mode,
        stage=job.stage,
        status=job.status,
        error_category=job.error_category,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        next_attempt_at=job.next_attempt_at,
        attempts=job.attempts,
    )


@router.post("/articles/{article_id}/process", response_model=ProcessResponse, status_code=202)
async def process_article_request(
    article_id: uuid.UUID, payload: ProcessRequest, db: Db, _mutation: Mutation
) -> ProcessResponse:
    if await db.get(Article, article_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    job, reused = await request_processing(db, article_id, payload.mode)
    await db.commit()
    return ProcessResponse(job_id=job.id, status=job.status, reused=reused)


@router.get("/jobs", response_model=JobPage)
async def list_jobs(
    db: Db,
    _auth: Auth,
    article_id: uuid.UUID | None = None,
    stage: Annotated[str | None, Query(pattern="^(fetch|extract)$")] = None,
    job_status: Annotated[str | None, Query(alias="status")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> JobPage:
    query = (
        select(ArticleProcessingJob)
        .options(
            selectinload(ArticleProcessingJob.article), selectinload(ArticleProcessingJob.attempts)
        )
        .order_by(ArticleProcessingJob.created_at.desc(), ArticleProcessingJob.id.desc())
    )
    if article_id:
        query = query.where(ArticleProcessingJob.article_id == article_id)
    if stage:
        query = query.where(ArticleProcessingJob.stage == stage)
    if job_status:
        query = query.where(ArticleProcessingJob.status == job_status)
    if cursor:
        created, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                ArticleProcessingJob.created_at < created,
                and_(ArticleProcessingJob.created_at == created, ArticleProcessingJob.id < item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).unique().all())
    next_cursor = (
        encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return JobPage(items=[_response(row) for row in rows[:limit]], next_cursor=next_cursor)


@router.get("/jobs/backlog", response_model=BacklogResponse)
async def backlog(db: Db, _auth: Auth) -> BacklogResponse:
    rows = (
        await db.execute(
            select(ArticleProcessingJob.status, func.count()).group_by(ArticleProcessingJob.status)
        )
    ).all()
    counts: dict[str, int] = {name: count for name, count in rows}
    return BacklogResponse(
        **{name: counts.get(name, 0) for name in ("queued", "running", "retrying", "failed")}
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: Db, _auth: Auth) -> JobResponse:
    job = await db.scalar(
        select(ArticleProcessingJob)
        .where(ArticleProcessingJob.id == job_id)
        .options(
            selectinload(ArticleProcessingJob.article), selectinload(ArticleProcessingJob.attempts)
        )
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return _response(job)


@router.post("/jobs/{job_id}/retry", response_model=ProcessResponse, status_code=202)
async def retry_job(job_id: uuid.UUID, db: Db, _mutation: Mutation) -> ProcessResponse:
    try:
        job, reused = await retry_processing(db, job_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found") from None
    await db.commit()
    return ProcessResponse(job_id=job.id, status=job.status, reused=reused)
