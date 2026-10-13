import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.clustering.engine import Assignment, RuleClusterer, StoryClusterer
from app.clustering.models import ArticleClusterState, ClusterJob
from app.clustering.service import CLUSTER_RETRIES
from app.core.config import get_settings
from app.db.session import session_factory
from app.jobs.leasing import (
    heartbeat,
    heartbeat_interval,
    holds,
    record_failure,
    release,
    renew,
)
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
        if job is None or not holds(job, token, datetime.now(UTC)):
            return None
        state = await db.get(ArticleClusterState, job.state_id)
        if state is None or state.requested_generation != job.generation:
            return None
        return LoadedJob(job.id, state.id, job.article_id, job.generation, job.algorithm_version)


async def _renew_lease(job_id: uuid.UUID, token: str) -> bool:
    lease_seconds = get_settings().clustering_lease_seconds
    async with session_factory() as db, db.begin():
        job = await db.scalar(select(ClusterJob).where(ClusterJob.id == job_id).with_for_update())
        return renew(job, token, datetime.now(UTC), lease_seconds)


async def _publish(loaded: LoadedJob, token: str) -> Assignment | None:
    """Assign the article and commit the membership, job state, and reindexing together.

    The isolation level stays at READ COMMITTED on purpose: `assign` serialises on an
    advisory lock, and `pg_advisory_xact_lock` takes its snapshot before the lock is
    granted, so REPEATABLE READ would make every queued job rebuild state a predecessor
    already committed. `test_concurrent_workers_converge_on_one_consistent_cluster` guards it.
    """
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
                or not holds(job, token, now)
                or state.requested_generation != loaded.generation
            ):
                raise StaleClaim
            job.status = "succeeded"
            release(job)
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
        if job is None or not holds(job, token, now):
            return
        state = await db.get(ArticleClusterState, job.state_id, with_for_update=True)
        record_failure(
            job,
            now,
            category,
            str(exc),
            CLUSTER_RETRIES,
            attempts=job.attempt_count,
            permanent=permanent,
        )
        if state is not None:
            state.status = job.status


async def process_job(job_id: uuid.UUID, token: str) -> None:
    finished = asyncio.Event()
    interval = heartbeat_interval(get_settings().clustering_lease_seconds)
    beating = asyncio.create_task(
        heartbeat(lambda: _renew_lease(job_id, token), interval, finished)
    )
    try:
        loaded = await _load_job(job_id, token)
        if loaded is None:
            return
        await _publish(loaded, token)
    except Exception as exc:
        await _record_failure(job_id, token, exc)
    finally:
        finished.set()
        await beating
