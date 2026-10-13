"""When an article is complete: every processing step that applies to it has finished.

Watches count an article from this moment, not from its discovery, so one whose entities arrive
later is still counted, and only once (fix plan Γ3). A step that failed for good counts as
finished: the article's title and text are searchable without it.
"""

import uuid
from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.articles.models import ACTIVE_STATUSES, Article, ArticleProcessingJob
from app.nlp.models import ArticleNlpState


async def lock_article(db: AsyncSession, article_id: uuid.UUID) -> None:
    """Hold the article row until commit. A finishing step takes it before deciding whether the
    article is complete, so of two steps finishing at once the second sees the first's outcome."""
    await db.execute(select(Article.id).where(Article.id == article_id).with_for_update())


async def mark_enriched(db: AsyncSession, article_id: uuid.UUID, now: datetime) -> bool:
    """Write `enriched_at` once, when no step is queued, running or waiting to retry.

    The caller holds the article row (`lock_article`) and requests indexing afterwards. A later
    reprocessing leaves the date as it is: the article was complete already.
    """
    pending = exists().where(
        ArticleProcessingJob.article_id == article_id,
        ArticleProcessingJob.status.in_(ACTIVE_STATUSES),
    ) | exists().where(
        ArticleNlpState.article_id == article_id,
        ArticleNlpState.status.in_(ACTIVE_STATUSES),
    )
    requested = exists().where(ArticleNlpState.article_id == article_id)
    result = await db.execute(
        update(Article)
        .where(Article.id == article_id, Article.enriched_at.is_(None), requested, ~pending)
        .values(enriched_at=now)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
