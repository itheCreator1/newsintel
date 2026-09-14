import os
import uuid

import pytest
from sqlalchemy import select

from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.nlp.models import ArticleNlpState, NlpJob
from app.nlp.service import request_article_nlp

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_unchanged_input_does_not_create_redundant_nlp_generations() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="A sufficiently descriptive title for deterministic NLP intent",
            normalized_title_hash=uuid.uuid4().hex,
        )
        feed = Feed(
            name="NLP source",
            url=f"https://example.test/{uuid.uuid4()}.xml",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        db.add_all([article, feed])
        await db.flush()
        db.add(
            FeedArticle(
                feed_id=feed.id,
                article_id=article.id,
                feed_title=article.title,
                feed_url=article.original_url,
                description="A stable description with enough words to annotate.",
                metadata_json={},
            )
        )
        await db.flush()

        first = await request_article_nlp(db, article.id)
        second = await request_article_nlp(db, article.id)
        article_id = article.id

    assert first == 4
    assert second == 0
    async with session_factory() as db:
        states = list(
            (
                await db.scalars(
                    select(ArticleNlpState).where(ArticleNlpState.article_id == article_id)
                )
            ).all()
        )
        jobs = list((await db.scalars(select(NlpJob).where(NlpJob.article_id == article_id))).all())
    assert len(states) == 4
    assert {state.requested_generation for state in states} == {1}
    assert len(jobs) == 4


async def test_changed_input_supersedes_old_jobs_and_requests_new_generation() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Original title",
            normalized_title_hash=uuid.uuid4().hex,
        )
        db.add(article)
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("language",))
        article.title = "Changed title"
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("language",))
        article_id = article.id

    async with session_factory() as db:
        state = await db.scalar(
            select(ArticleNlpState).where(
                ArticleNlpState.article_id == article_id,
                ArticleNlpState.processor_name == "language",
            )
        )
        jobs = list(
            (
                await db.scalars(
                    select(NlpJob)
                    .where(NlpJob.article_id == article_id)
                    .order_by(NlpJob.generation)
                )
            ).all()
        )
    assert state is not None and state.requested_generation == 2
    assert [job.status for job in jobs] == ["superseded", "queued"]
