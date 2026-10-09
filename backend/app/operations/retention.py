"""Deletes succeeded job history the application no longer reads. Failed rows are always kept,
and so is the newest row each reader depends on:

- article jobs: the newest per article, since automatic processing refuses an article with any job;
- NLP and cluster jobs: the newest succeeded one per article (and processor), whose runs are the
  provenance the article page shows;
- search deliveries: only those of `retained` targets (past rebuilds), the rest are indexing state.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.models import Session
from app.clustering.models import ClusterJob
from app.db.session import session_factory
from app.feeds.models import ArticleProcessingJob
from app.nlp.models import NlpJob
from app.operations.models import MaintenanceRun
from app.search.models import SearchDelivery, SearchIndexTarget

JOB_RETENTION = timedelta(days=30)
# Rows per table per call; the scheduler calls hourly, so a large first backlog drains over hours.
BATCH = 5000

_DONE = ("succeeded", "superseded")


def _candidates(cutoff: datetime) -> dict[str, tuple[Any, Select[tuple[uuid.UUID]]]]:
    newer_article = aliased(ArticleProcessingJob)
    newer_nlp = aliased(NlpJob)
    newer_cluster = aliased(ClusterJob)
    return {
        "article_processing_jobs": (
            ArticleProcessingJob,
            select(ArticleProcessingJob.id).where(
                ArticleProcessingJob.status == "succeeded",
                ArticleProcessingJob.completed_at < cutoff,
                exists().where(
                    newer_article.article_id == ArticleProcessingJob.article_id,
                    newer_article.created_at > ArticleProcessingJob.created_at,
                ),
            ),
        ),
        "nlp_jobs": (
            NlpJob,
            select(NlpJob.id).where(
                NlpJob.status.in_(_DONE),
                NlpJob.created_at < cutoff,
                exists().where(
                    newer_nlp.article_id == NlpJob.article_id,
                    newer_nlp.processor_name == NlpJob.processor_name,
                    newer_nlp.generation > NlpJob.generation,
                    newer_nlp.status == "succeeded",
                ),
            ),
        ),
        "cluster_jobs": (
            ClusterJob,
            select(ClusterJob.id).where(
                ClusterJob.status.in_(_DONE),
                ClusterJob.created_at < cutoff,
                exists().where(
                    newer_cluster.article_id == ClusterJob.article_id,
                    newer_cluster.generation > ClusterJob.generation,
                    newer_cluster.status == "succeeded",
                ),
            ),
        ),
        "search_deliveries": (
            SearchDelivery,
            select(SearchDelivery.id)
            .join(SearchIndexTarget, SearchIndexTarget.id == SearchDelivery.target_id)
            .where(
                SearchIndexTarget.role == "retained",
                SearchDelivery.status == "succeeded",
                SearchDelivery.updated_at < cutoff,
            ),
        ),
        "sessions": (
            Session,
            select(Session.id).where(or_(Session.expires_at < cutoff, Session.revoked_at < cutoff)),
        ),
        "maintenance_runs": (
            MaintenanceRun,
            select(MaintenanceRun.id).where(MaintenanceRun.started_at < cutoff),
        ),
    }


async def prune_history(db: AsyncSession, now: datetime) -> dict[str, int]:
    """Delete up to `BATCH` rows per table; the caller commits. Returns the count per table."""
    deleted = {}
    for name, (model, ids) in _candidates(now - JOB_RETENTION).items():
        result = await db.execute(
            delete(model)
            .where(model.id.in_(ids.limit(BATCH)))
            .execution_options(synchronize_session=False)
        )
        deleted[name] = result.rowcount  # type: ignore[attr-defined]
    return deleted


async def run_retention(now: datetime) -> dict[str, int]:
    """Prune once and record it in `maintenance_runs`, the Processes page's history cleanup card.

    A failure is recorded too (the class and message), then raised for the caller to log.
    """
    try:
        async with session_factory() as db, db.begin():
            deleted = await prune_history(db, now)
    except Exception as exc:
        async with session_factory() as db, db.begin():
            db.add(
                MaintenanceRun(
                    kind="retention", started_at=now, finished_at=datetime.now(now.tzinfo),
                    error_message=f"{type(exc).__name__}: {exc}"[:1000],
                )
            )  # fmt: skip
        raise
    async with session_factory() as db, db.begin():
        db.add(
            MaintenanceRun(
                kind="retention", started_at=now, finished_at=datetime.now(now.tzinfo),
                deleted=deleted,
            )
        )  # fmt: skip
    return deleted
