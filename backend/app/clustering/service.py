import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.engine import CLUSTER_ALGORITHM_VERSION
from app.clustering.models import ArticleClusterState, ClusterJob
from app.db.session import session_factory
from app.feeds.models import Article
from app.nlp.service import next_retry_at

MAX_ATTEMPTS = 5

__all__ = [
    "CLUSTER_ALGORITHM_VERSION",
    "MAX_ATTEMPTS",
    "claim_job",
    "count_recluster_selection",
    "job_due",
    "next_retry_at",
    "request_clustering",
    "run_recluster",
]


async def request_clustering(db: AsyncSession, article_id: uuid.UUID) -> int:
    """Record the intent to (re)cluster an article and queue exactly one job for it.

    Callers only reach this once the inputs an assignment depends on have changed,
    so every call supersedes any in-flight job and requests a new generation.
    """
    state = await db.scalar(
        select(ArticleClusterState)
        .where(ArticleClusterState.article_id == article_id)
        .with_for_update()
    )
    if state is None:
        state = ArticleClusterState(
            article_id=article_id, algorithm_version=CLUSTER_ALGORITHM_VERSION
        )
        db.add(state)
        await db.flush()
    else:
        state.requested_generation += 1
        state.algorithm_version = CLUSTER_ALGORITHM_VERSION
        state.status = "queued"
        await db.execute(
            update(ClusterJob)
            .where(
                ClusterJob.state_id == state.id,
                ClusterJob.status.in_(("queued", "running", "retrying")),
            )
            .values(status="superseded", claim_token=None, claim_expires_at=None)
        )
    db.add(
        ClusterJob(
            state_id=state.id,
            article_id=article_id,
            generation=state.requested_generation,
            algorithm_version=CLUSTER_ALGORITHM_VERSION,
        )
    )
    await db.flush()
    return 1


def job_due(now: datetime):  # type: ignore[no-untyped-def]
    return (
        ClusterJob.status.in_(("queued", "running", "retrying"))
        & (ClusterJob.next_attempt_at <= now)
        & or_(ClusterJob.claim_expires_at.is_(None), ClusterJob.claim_expires_at <= now)
    )


async def claim_job(
    db: AsyncSession, job_id: uuid.UUID, lease_seconds: int
) -> tuple[ClusterJob, str] | None:
    now = datetime.now(UTC)
    job = await db.scalar(select(ClusterJob).where(ClusterJob.id == job_id).with_for_update())
    if (
        job is None
        or job.status not in ("queued", "running", "retrying")
        or job.next_attempt_at > now
        or (job.claim_expires_at is not None and job.claim_expires_at > now)
    ):
        return None
    state = await db.get(ArticleClusterState, job.state_id, with_for_update=True)
    if state is None or state.requested_generation != job.generation:
        job.status = "superseded"
        await db.commit()
        return None
    token = uuid.uuid4().hex
    job.claim_token = token
    job.claim_expires_at = now + timedelta(seconds=lease_seconds)
    job.status = "running"
    job.attempt_count += 1
    state.status = "running"
    await db.commit()
    return job, token


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("reclustering dates must include a UTC offset")
    return parsed.astimezone(UTC)


def _apply_selection(query, selection: dict[str, object]):  # type: ignore[no-untyped-def]
    article_ids = selection.get("article_ids")
    if isinstance(article_ids, list):
        query = query.where(Article.id.in_([uuid.UUID(str(value)) for value in article_ids]))
    from_date = _parse_datetime(selection.get("from_date"))
    to_date = _parse_datetime(selection.get("to_date"))
    if from_date:
        query = query.where(Article.first_discovered_at >= from_date)
    if to_date:
        query = query.where(Article.first_discovered_at < to_date)
    return query


def _validate_selection(selection: dict[str, object]) -> None:
    if not any(key in selection for key in ("article_ids", "from_date", "to_date", "all")):
        raise ValueError("reclustering requires article IDs, a UTC date range, or explicit all")


async def count_recluster_selection(selection: dict[str, object]) -> int:
    _validate_selection(selection)
    async with session_factory() as db:
        query = _apply_selection(select(func.count()).select_from(Article), selection)
        return int(await db.scalar(query) or 0)


async def run_recluster(selection: dict[str, object], *, batch_size: int = 100) -> int:
    """Queue clustering for every selected article, walking a bounded keyset cursor."""
    _validate_selection(selection)
    if batch_size < 1 or batch_size > 500:
        raise ValueError("recluster batch size must be between 1 and 500")
    cursor: uuid.UUID | None = None
    queued = 0
    while True:
        async with session_factory() as db, db.begin():
            query = _apply_selection(
                select(Article.id).order_by(Article.id).limit(batch_size), selection
            )
            if cursor is not None:
                query = query.where(Article.id > cursor)
            ids = list((await db.scalars(query)).all())
            for article_id in ids:
                queued += await request_clustering(db, article_id)
        if len(ids) < batch_size:
            return queued
        cursor = ids[-1]
