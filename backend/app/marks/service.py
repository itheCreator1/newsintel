import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.marks.models import ArticleMark
from app.marks.schemas import ArticleMarkResponse, ArticleMarkUpdate

if TYPE_CHECKING:
    from app.articles.schemas import ArticleResponse
    from app.search.schemas import SearchResult


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


def apply_marks(items: Sequence["ArticleResponse"], marks: Mapping[uuid.UUID, ArticleMark]) -> None:
    """Sets read and starred on each article that has a mark; the rest stay unread, unstarred."""
    for item in items:
        if mark := marks.get(item.id):
            item.read, item.starred = mark.read_at is not None, mark.starred


async def with_marks(db: AsyncSession, items: Sequence["ArticleResponse"]) -> None:
    """The reader's marks on a list of articles, in one query."""
    apply_marks(items, await marks_for(db, [item.id for item in items]))


async def with_result_marks(db: AsyncSession, items: Sequence["SearchResult"]) -> None:
    """The same for search results, which also carry the note."""
    marks = await marks_for(db, [item.article_id for item in items])
    for item in items:
        if mark := marks.get(item.article_id):
            item.read, item.starred, item.note = mark.read_at is not None, mark.starred, mark.note


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
