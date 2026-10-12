import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.marks.models import ArticleMark
from app.marks.schemas import ArticleMarkResponse, ArticleMarkUpdate


def mark_response(article_id: uuid.UUID, mark: ArticleMark | None) -> ArticleMarkResponse:
    return ArticleMarkResponse(
        article_id=article_id,
        read=mark is not None and mark.read_at is not None,
        read_at=mark.read_at if mark else None,
        starred=bool(mark and mark.starred),
        note=mark.note if mark else None,
    )


async def marks_for(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ArticleMark]:
    if not ids:
        return {}
    rows = await db.scalars(select(ArticleMark).where(ArticleMark.article_id.in_(ids)))
    return {row.article_id: row for row in rows}


async def update_mark(
    db: AsyncSession, article_id: uuid.UUID, change: ArticleMarkUpdate
) -> ArticleMark:
    mark = await db.get(ArticleMark, article_id, with_for_update=True)
    if mark is None:
        mark = ArticleMark(article_id=article_id, starred=False)
        db.add(mark)
    sent = change.model_fields_set
    if "read" in sent and change.read is not None:
        # The first time it was read is kept; marking it read again changes nothing.
        mark.read_at = (mark.read_at or datetime.now(UTC)) if change.read else None
    if "starred" in sent and change.starred is not None:
        mark.starred = change.starred
    if "note" in sent:
        mark.note = change.note
    await db.flush()
    return mark
