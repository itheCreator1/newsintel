"""Article lists as CSV or JSON downloads: title, address, date, sources and main entities."""

import csv
import io
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.feeds.models import Article, FeedArticle
from app.nlp.models import ArticleEntity, Entity

# A download holds at most this many articles, in the order of the list it comes from.
EXPORT_LIMIT = 5000
ENTITIES_PER_ARTICLE = 5
CHUNK = 1000
ExportFormat = Literal["csv", "json"]
FIELDS = ("article_id", "title", "url", "date", "sources", "entities")
MEDIA_TYPES = {"csv": "text/csv", "json": "application/json"}


@dataclass(frozen=True)
class ExportRow:
    article_id: uuid.UUID
    title: str
    url: str
    date: datetime
    sources: list[str]
    entities: list[str]


async def _top_entities(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    rank = (
        func.row_number()
        .over(
            partition_by=ArticleEntity.article_id,
            order_by=(
                ArticleEntity.relevance.desc(),
                ArticleEntity.occurrence_count.desc(),
                Entity.name,
                Entity.id,
            ),
        )
        .label("rank")
    )
    ranked = (
        select(ArticleEntity.article_id, Entity.name.label("name"), rank)
        .join(Entity, Entity.id == ArticleEntity.entity_id)
        .where(
            ArticleEntity.article_id.in_(ids),
            ArticleEntity.is_current.is_(True),
            Entity.hidden.is_(False),
        )
        .subquery()
    )
    rows = await db.execute(
        select(ranked.c.article_id, ranked.c.name)
        .where(ranked.c.rank <= ENTITIES_PER_ARTICLE)
        .order_by(ranked.c.article_id, ranked.c.rank)
    )
    found: dict[uuid.UUID, list[str]] = defaultdict(list)
    for article_id, name in rows:
        found[article_id].append(name)
    return found


async def export_rows(db: AsyncSession, ids: Sequence[uuid.UUID]) -> list[ExportRow]:
    """The rows for these articles, in the order given; ids that no longer exist are left out."""
    articles: dict[uuid.UUID, Article] = {}
    entities: dict[uuid.UUID, list[str]] = {}
    for start in range(0, len(ids), CHUNK):
        chunk = list(ids[start : start + CHUNK])
        found = await db.scalars(
            select(Article)
            .where(Article.id.in_(chunk))
            .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        )
        articles.update({article.id: article for article in found})
        entities.update(await _top_entities(db, chunk))
    return [
        ExportRow(
            article_id=article.id,
            title=article.title,
            url=article.original_url,
            date=article.published_at or article.first_discovered_at,
            sources=sorted({item.feed.name for item in article.discoveries}),
            entities=entities.get(article.id, []),
        )
        for article in (articles.get(item) for item in ids)
        if article is not None
    ]


def _cell(value: str) -> str:
    """A spreadsheet runs a cell starting with = + - @ as a formula; a quote keeps it text."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


def render(rows: Sequence[ExportRow], format: ExportFormat) -> bytes:
    if format == "json":
        return json.dumps(
            [
                {
                    "article_id": str(row.article_id),
                    "title": row.title,
                    "url": row.url,
                    "date": row.date.isoformat(),
                    "sources": row.sources,
                    "entities": row.entities,
                }
                for row in rows
            ],
            ensure_ascii=False,
            indent=2,
        ).encode()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow(
            [
                str(row.article_id),
                _cell(row.title),
                _cell(row.url),
                row.date.isoformat(),
                "; ".join(_cell(value) for value in row.sources),
                "; ".join(_cell(value) for value in row.entities),
            ]
        )
    # The byte order mark tells spreadsheets the file is UTF-8, so Greek names open correctly.
    return buffer.getvalue().encode("utf-8-sig")


def export_response(
    rows: Sequence[ExportRow], format: ExportFormat, name: str, *, today: date | None = None
) -> Response:
    day = (today or datetime.now(UTC).date()).isoformat()
    return Response(
        render(rows, format),
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="newsintel-{name}-{day}.{format}"'},
    )
