import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.clustering.engine import Assignment, RuleClusterer, StoryClusterer
from app.clustering.models import ArticleClusterState, ClusterJob
from app.clustering.service import MAX_ATTEMPTS, next_retry_at
from app.core.config import get_settings
from app.db.session import session_factory
from app.search.service import request_indexing

_CLUSTERER: StoryClusterer = RuleClusterer()


def clusterer() -> StoryClusterer:
    return _CLUSTERER


class StaleClaim(Exception):
    """The job, its lease, or its generation changed while the assignment ran."""


@dataclass(frozen=True)
class LoadedJob:
    job_id: uuid.UUID
    state_id: uuid.UUID
    article_id: uuid.UUID
    generation: int
    algorithm_version: str


async def _load_job(job_id: uuid.UUID, token: str) -> LoadedJob | None:
    async with session_factory() as db:
        job = await db.get(ClusterJob, job_id)
        if (
            job is None
            or job.claim_token != token
            or job.claim_expires_at is None
            or job.claim_expires_at <= datetime.now(UTC)
        ):
            return None
        state = await db.get(ArticleClusterState, job.state_id)
        if state is None or state.requested_generation != job.generation:
            return None
        return LoadedJob(job.id, state.id, job.article_id, job.generation, job.algorithm_version)


async def _renew_lease(job_id: uuid.UUID, token: str) -> bool:
    settings = get_settings()
    async with session_factory() as db, db.begin():
        job = await db.scalar(select(ClusterJob).where(ClusterJob.id == job_id).with_for_update())
        if job is None or job.claim_token != token or job.status != "running":
            return False
        if job.claim_expires_at is None or job.claim_expires_at <= datetime.now(UTC):
            return False
        job.claim_expires_at = datetime.now(UTC) + timedelta(
            seconds=settings.clustering_lease_seconds
        )
        return True


async def _heartbeat(job_id: uuid.UUID, token: str, finished: asyncio.Event) -> None:
    interval = max(1.0, min(30.0, get_settings().clustering_lease_seconds / 3))
    while True:
        try:
            await asyncio.wait_for(finished.wait(), timeout=interval)
            return
        except TimeoutError:
            if not await _renew_lease(job_id, token):
                return


async def _publish(loaded: LoadedJob, token: str) -> Assignment | None:
    """Assign the article and commit the membership, job state, and reindexing together."""
    now = datetime.now(UTC)
    try:
        async with session_factory() as db, db.begin():
            assignment = await clusterer().assign(db, loaded.article_id)
            job = await db.scalar(
                select(ClusterJob).where(ClusterJob.id == loaded.job_id).with_for_update()
            )
            state = await db.get(ArticleClusterState, loaded.state_id, with_for_update=True)
            if (
                job is None
                or state is None
                or job.claim_token != token
                or job.claim_expires_at is None
                or job.claim_expires_at <= now
                or state.requested_generation != loaded.generation
            ):
                raise StaleClaim
            job.status = "succeeded"
            job.claim_token = None
            job.claim_expires_at = None
            job.error_category = None
            job.error_message = None
            job.completed_at = now
            state.completed_generation = job.generation
            state.status = "succeeded"
            for article_id in assignment.reindex_article_ids:
                await request_indexing(db, article_id)
            return assignment
    except StaleClaim:
        return None


async def _record_failure(job_id: uuid.UUID, token: str, exc: Exception) -> None:
    now = datetime.now(UTC)
    permanent = isinstance(exc, LookupError)
    category = "article_missing" if permanent else "clustering"
    async with session_factory() as db, db.begin():
        job = await db.scalar(select(ClusterJob).where(ClusterJob.id == job_id).with_for_update())
        if (
            job is None
            or job.claim_token != token
            or job.claim_expires_at is None
            or job.claim_expires_at <= now
        ):
            return
        state = await db.get(ArticleClusterState, job.state_id, with_for_update=True)
        job.claim_token = None
        job.claim_expires_at = None
        job.error_category = category
        job.error_message = str(exc)[:1000]
        if permanent or job.attempt_count >= MAX_ATTEMPTS:
            job.status = "failed"
            job.completed_at = now
        else:
            job.status = "retrying"
            job.next_attempt_at = next_retry_at(now, job.attempt_count)
        if state is not None:
            state.status = job.status


async def process_job(job_id: uuid.UUID, token: str) -> None:
    finished = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(job_id, token, finished))
    try:
        loaded = await _load_job(job_id, token)
        if loaded is None:
            return
        await _publish(loaded, token)
    except Exception as exc:
        await _record_failure(job_id, token, exc)
    finally:
        finished.set()
        await heartbeat
