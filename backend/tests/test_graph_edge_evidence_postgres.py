import os
import uuid
from datetime import UTC, datetime

import pytest

from app.clustering.models import StoryCluster
from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import article_response
from app.graph.service import articles_by_id, clusters_by_id

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)


async def test_evidence_hydration_returns_canonical_records_and_skips_unknown_ids() -> None:
    async with session_factory() as db, db.begin():
        feed = Feed(
            name="Evidence Wire",
            url=f"https://example.test/{uuid.uuid4()}",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Edge evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=BASE,
            first_discovered_at=BASE,
        )
        db.add_all([feed, article])
        await db.flush()
        db.add(
            FeedArticle(
                feed_id=feed.id,
                article_id=article.id,
                feed_title=article.title,
                feed_url=article.original_url,
                description=None,
                metadata_json={},
            )
        )
        cluster = StoryCluster(
            algorithm_version="test",
            article_count=2,
            source_count=2,
            representative_article_id=article.id,
        )
        db.add(cluster)
        await db.flush()

        stale = uuid.uuid4()
        articles = await articles_by_id(db, [article.id, stale])
        clusters = await clusters_by_id(db, [cluster.id, stale])

        assert list(articles) == [article.id]
        assert [item.feed_name for item in article_response(articles[article.id]).provenance] == [
            "Evidence Wire"
        ]
        assert list(clusters) == [cluster.id]
        assert clusters[cluster.id].representative_article_id == article.id
        assert await articles_by_id(db, []) == {}
        assert await clusters_by_id(db, []) == {}
