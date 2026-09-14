import os
import uuid

import pytest
from sqlalchemy import func, select

from app.db.session import session_factory
from app.feeds.models import Article, Feed
from app.search.models import (
    ArticleSearchState,
    SearchDelivery,
    SearchIndexTarget,
    SourceSearchRefresh,
)
from app.search.service import request_indexing, request_source_refresh

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_index_intent_updates_each_registered_target_atomically() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Search intent",
            normalized_title_hash=uuid.uuid4().hex,
        )
        target = SearchIndexTarget(
            index_name=f"articles-v1-{uuid.uuid4()}", schema_version=1, role="current"
        )
        db.add_all([article, target])
        await db.flush()
        assert await request_indexing(db, article.id) == 1
        assert await request_indexing(db, article.id) == 2
        article_id = article.id
        target_id = target.id

    async with session_factory() as db:
        state = await db.get(ArticleSearchState, article_id)
        delivery = await db.scalar(
            select(SearchDelivery).where(
                SearchDelivery.article_id == article_id,
                SearchDelivery.target_id == target_id,
            )
        )
        assert state is not None and state.requested_revision == 2
        assert delivery is not None
        assert (delivery.requested_revision, delivery.indexed_revision, delivery.status) == (
            2,
            0,
            "queued",
        )


async def test_source_refresh_requests_are_deduplicated_while_active() -> None:
    async with session_factory() as db, db.begin():
        feed = Feed(
            name="Refresh source",
            url=f"https://example.test/{uuid.uuid4()}.xml",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        db.add(feed)
        await db.flush()
        await request_source_refresh(db, feed.id)
        await db.flush()
        await request_source_refresh(db, feed.id)
        feed_id = feed.id

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(SourceSearchRefresh)
            .where(SourceSearchRefresh.feed_id == feed_id)
        )
        assert count == 1
