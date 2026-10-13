"""An article is complete, and watches count it, once every processing step has finished.

Watches used to count an article by when it was discovered, so one whose entities arrived after
the evaluation had passed its discovery time was never counted (fix plan Γ3).
"""

import asyncio
import os
import uuid
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.articles import processing
from app.articles.models import Article
from app.articles.processing import ArticleHttpStatus, process_claim
from app.articles.service import claim_due_job, request_processing
from app.db.session import session_factory
from app.feeds.models import Feed, FeedArticle
from app.nlp import execution
from app.nlp.models import ArticleNlpState, NlpJob
from app.nlp.processors import ConfigurationError
from app.nlp.service import PROCESSORS, claim_job, request_article_nlp

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

LAST = PROCESSORS[-1]


async def _article(fetching_mode: str = "rss") -> uuid.UUID:
    """A discovered article with its NLP queued, and its full text too unless the feed is RSS."""
    async with session_factory() as db, db.begin():
        feed = Feed(
            name=f"Enrichment {uuid.uuid4().hex[:8]}",
            url=f"https://example.test/{uuid.uuid4()}.xml",
            tags=[],
            poll_interval_minutes=30,
            fetching_mode=fetching_mode,
        )
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Harbour workers agree a new contract with the port authority",
            normalized_title_hash=uuid.uuid4().hex,
        )
        db.add_all([feed, article])
        await db.flush()
        db.add(
            FeedArticle(
                feed_id=feed.id,
                article_id=article.id,
                feed_title=article.title,
                feed_url=article.original_url,
                description="The agreement ends a week of strikes at the container terminal.",
            )
        )
        await db.flush()
        await request_article_nlp(db, article.id)
        if fetching_mode != "rss":
            await request_processing(db, article.id, fetching_mode, automatic=True)
        return article.id


async def _job(article_id: uuid.UUID, processor: str) -> uuid.UUID:
    async with session_factory() as db:
        job_id = await db.scalar(
            select(NlpJob.id).where(
                NlpJob.article_id == article_id,
                NlpJob.processor_name == processor,
                NlpJob.status.in_(("queued", "retrying")),
            )
        )
    assert job_id is not None, processor
    return job_id


async def _run(article_id: uuid.UUID, processor: str) -> None:
    job_id = await _job(article_id, processor)
    async with session_factory() as db:
        claimed = await claim_job(db, job_id, lease_seconds=60)
    assert claimed is not None
    await execution.process_job(job_id, claimed[1])


async def _enriched_at(article_id: uuid.UUID) -> datetime | None:
    async with session_factory() as db:
        return await db.scalar(select(Article.enriched_at).where(Article.id == article_id))


async def test_enriched_at_is_written_once_when_the_last_step_finishes() -> None:
    article_id = await _article()
    for processor in PROCESSORS[:-1]:
        await _run(article_id, processor)
        assert await _enriched_at(article_id) is None, processor
    await _run(article_id, LAST)
    enriched_at = await _enriched_at(article_id)
    assert enriched_at is not None

    # Reprocessing runs every step again; the article was complete already.
    async with session_factory() as db, db.begin():
        assert await request_article_nlp(db, article_id, force=True) == len(PROCESSORS)
    for processor in PROCESSORS:
        await _run(article_id, processor)
    assert await _enriched_at(article_id) == enriched_at


async def test_a_step_that_fails_for_good_still_completes_the_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_processor = execution._run_processor

    def broken_entities(loaded: execution.LoadedJob) -> object:
        if loaded.processor_name == "entities":
            raise ConfigurationError("the NER model is missing")
        return run_processor(loaded)

    monkeypatch.setattr(execution, "_run_processor", broken_entities)
    article_id = await _article()
    for processor in PROCESSORS:
        await _run(article_id, processor)
    async with session_factory() as db:
        state = await db.scalar(
            select(ArticleNlpState.status).where(
                ArticleNlpState.article_id == article_id,
                ArticleNlpState.processor_name == "entities",
            )
        )
    assert state == "failed"
    # Its title and words are searchable; a watch on them must not miss it.
    assert await _enriched_at(article_id) is not None


async def test_a_step_that_will_be_retried_holds_the_article_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_processor = execution._run_processor

    def flaky_keywords(loaded: execution.LoadedJob) -> object:
        if loaded.processor_name == "keywords":
            raise RuntimeError("a passing fault")
        return run_processor(loaded)

    monkeypatch.setattr(execution, "_run_processor", flaky_keywords)
    article_id = await _article()
    for processor in PROCESSORS:
        await _run(article_id, processor)
    assert await _enriched_at(article_id) is None


async def test_an_article_waits_for_its_full_text_and_is_complete_when_it_never_comes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def gone(url: str, settings: object) -> object:
        raise ArticleHttpStatus(404, None)

    monkeypatch.setattr(processing, "fetch_page_http", gone)
    article_id = await _article("full_text")
    for processor in PROCESSORS:
        await _run(article_id, processor)
    assert await _enriched_at(article_id) is None  # the full text may still change every step

    async with session_factory() as db:
        job_id = await db.scalar(
            select(processing.ArticleProcessingJob.id).where(
                processing.ArticleProcessingJob.article_id == article_id
            )
        )
        assert job_id is not None
        claimed = await claim_due_job(db, job_id, 300)
    assert claimed is not None
    await process_claim(job_id, claimed[1])
    assert await _enriched_at(article_id) is not None


async def test_a_finishing_step_waits_for_the_article_so_the_last_two_see_each_other() -> None:
    # Two steps finishing at once each saw the other still running, and neither completed the
    # article. Each finishing step first locks the article row, so the second sees the first.
    article_id = await _article()
    for processor in PROCESSORS[:-1]:
        await _run(article_id, processor)
    async with session_factory() as holder, holder.begin():
        await holder.execute(
            text("SELECT id FROM articles WHERE id = :id FOR UPDATE"), {"id": article_id}
        )
        last = asyncio.create_task(_run(article_id, LAST))
        await asyncio.sleep(1)
        assert not last.done()
    await asyncio.wait_for(last, 30)
    assert await _enriched_at(article_id) is not None


async def test_the_migration_marks_finished_articles_complete_and_goes_with_its_downgrade() -> None:
    finished, waiting = await _article(), await _article()
    for processor in PROCESSORS:
        await _run(finished, processor)
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0005")
    try:
        async with session_factory() as db:
            columns = await db.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'articles'"
                )
            )
            assert "enriched_at" not in set(columns)
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with session_factory() as db:
        discovered = await db.scalar(
            select(Article.first_discovered_at).where(Article.id == finished)
        )
    # An article finished before the upgrade counts from its discovery, as it did then.
    assert await _enriched_at(finished) == discovered
    assert await _enriched_at(waiting) is None
