"""Deletes succeeded job history the application no longer reads. Failed rows are always kept,
and so is the newest row each reader depends on:

- article jobs: the newest per article, since automatic processing refuses an article with any job;
- NLP and cluster jobs: the newest succeeded one per article (and processor), whose runs are the
  provenance the article page shows;
- search deliveries: only those of `retained` targets (past rebuilds), the rest are indexing state.
"""

from datetime import datetime, timedelta

from sqlalchemy import Select, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.models import Session
from app.clustering.models import ClusterJob
from app.feeds.models import ArticleProcessingJob
from app.nlp.models import NlpJob
from app.search.models import SearchDelivery, SearchIndexTarget

JOB_RETENTION = timedelta(days=30)
# Rows per table per call; the scheduler calls hourly, so a large first backlog drains over hours.
BATCH = 5000

_DONE = ("succeeded", "superseded")


def _candidates(cutoff: datetime) -> dict[str, tuple[type, Select]]:  # type: ignore[type-arg]
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
        # ponytail: nlp_jobs.created_at and search_deliveries.updated_at are unindexed, so these
        # scan hourly; add indexes in the next migration if the pruning shows up in the logs.
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
    }


async def prune_history(db: AsyncSession, now: datetime) -> dict[str, int]:
    """Delete up to `BATCH` rows per table; the caller commits. Returns the count per table."""
    deleted = {}
    for name, (model, ids) in _candidates(now - JOB_RETENTION).items():
        result = await db.execute(
            delete(model)
            .where(model.id.in_(ids.limit(BATCH)))  # type: ignore[attr-defined]
            .execution_options(synchronize_session=False)
        )
        deleted[name] = result.rowcount  # type: ignore[attr-defined]
    return deleted
