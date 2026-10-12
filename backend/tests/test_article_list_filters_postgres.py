"""The Articles list narrows by read and starred marks, language, full text and dates."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from event_fixtures import feed
from sqlalchemy import select, update
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article
from test_exports_postgres import _discover

from app.db.session import session_factory
from app.feeds.models import Article, ArticleContent
from app.nlp.models import ArticleLanguageAnnotation, NlpProcessorRun

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _seeded() -> tuple[str, dict[str, uuid.UUID]]:
    """One fresh source with three articles: starred and read, Greek with text, plain."""
    async with session_factory() as db, db.begin():
        source = await feed(db)
        ids = {name: await _article(db, []) for name in ("starred", "greek", "plain")}
        for name, day in (("starred", 3), ("greek", 10), ("plain", 20)):
            await db.execute(
                update(Article)
                .where(Article.id == ids[name])
                .values(first_discovered_at=datetime(2026, 1, day, 12, tzinfo=UTC))
            )
            await _discover(db, ids[name], source)
        at = datetime(2026, 1, 10, tzinfo=UTC)
        db.add(
            ArticleContent(
                article_id=ids["greek"],
                text="Κείμενο",
                content_hash="a" * 64,
                extractor_name="test",
                extractor_version="1",
                extracted_at=at,
                last_content_change_at=at,
            )
        )
        run_id = await db.scalar(
            select(NlpProcessorRun.id).where(NlpProcessorRun.article_id == ids["greek"])
        )
        db.add(
            ArticleLanguageAnnotation(
                article_id=ids["greek"],
                run_id=run_id,
                language="el",
                confidence=0.99,
                margin=0.5,
                input_fingerprint="a" * 64,
                is_current=True,
            )
        )
        source_id = str(source.id)
    async with _client() as client:
        await client.put(
            f"/articles/{ids['starred']}/mark", json={"read": True, "starred": True}, headers=CSRF
        )
    return source_id, ids


async def _titles(source_id: str, ids: dict[str, uuid.UUID], **params: object) -> list[str]:
    names = {str(value): key for key, value in ids.items()}
    async with _client() as client:
        response = await client.get("/articles", params={"feed_id": source_id, **params})
    assert response.status_code == 200, response.text
    return [names[item["id"]] for item in response.json()["items"]]


async def test_each_filter_narrows_the_list() -> None:
    source_id, ids = await _seeded()
    assert await _titles(source_id, ids) == ["plain", "greek", "starred"]
    assert await _titles(source_id, ids, unread="true") == ["plain", "greek"]
    assert await _titles(source_id, ids, starred="true") == ["starred"]
    assert await _titles(source_id, ids, has_text="true") == ["greek"]
    assert await _titles(source_id, ids, language="el") == ["greek"]
    # Dates are on discovery, the list's own order; before is exclusive, as in Search.
    assert await _titles(source_id, ids, after="2026-01-05", before="2026-01-20") == ["greek"]
    assert await _titles(source_id, ids, unread="true", language="el") == ["greek"]


async def test_filtered_pages_follow_one_another() -> None:
    source_id, ids = await _seeded()
    names = {str(value): key for key, value in ids.items()}
    async with _client() as client:
        first = (
            await client.get(
                "/articles", params={"feed_id": source_id, "unread": "true", "limit": 1}
            )
        ).json()
        second = (
            await client.get(
                "/articles",
                params={
                    "feed_id": source_id,
                    "unread": "true",
                    "limit": 1,
                    "cursor": first["next_cursor"],
                },
            )
        ).json()
    assert [names[item["id"]] for item in first["items"] + second["items"]] == ["plain", "greek"]
    assert second["next_cursor"] is None
