import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.nlp.execution import process_job
from app.nlp.models import ArticleLanguageAnnotation, ArticleNlpState, NlpJob
from app.nlp.reprocessing import create_reprocessing_run, scan_reprocessing
from app.nlp.service import claim_job, current_stop_words, request_article_nlp, update_stop_words

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


async def test_language_job_claim_and_publication_are_durable() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title=(
                "English reporting describes renewable energy policy and international markets "
                "with enough detail for confident local language detection"
            ),
            normalized_title_hash=uuid.uuid4().hex,
        )
        db.add(article)
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("language",))
        await db.flush()
        job = await db.scalar(select(NlpJob).where(NlpJob.article_id == article.id))
        assert job is not None
        job_id = job.id

    async with session_factory() as db:
        claimed = await claim_job(db, job_id, lease_seconds=60)
    assert claimed is not None
    _, token = claimed
    await process_job(job_id, token)

    async with session_factory() as db:
        job = await db.get(NlpJob, job_id)
        annotation = await db.scalar(
            select(ArticleLanguageAnnotation).where(
                ArticleLanguageAnnotation.article_id == article.id,
                ArticleLanguageAnnotation.is_current.is_(True),
            )
        )
    assert job is not None and job.status == "succeeded" and job.attempt_count == 1
    assert annotation is not None and annotation.language == "en"


async def test_expired_worker_cannot_publish_nlp_output() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="English article with many alphabetic words describing policy markets and news",
            normalized_title_hash=uuid.uuid4().hex,
        )
        db.add(article)
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("language",))
        await db.flush()
        job = await db.scalar(select(NlpJob).where(NlpJob.article_id == article.id))
        assert job is not None
        job_id = job.id

    async with session_factory() as db:
        claimed = await claim_job(db, job_id, lease_seconds=60)
        assert claimed is not None
        _, token = claimed
        job = await db.get(NlpJob, job_id, with_for_update=True)
        assert job is not None
        job.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    await process_job(job_id, token)
    async with session_factory() as db:
        annotation = await db.scalar(
            select(ArticleLanguageAnnotation).where(
                ArticleLanguageAnnotation.article_id == article.id
            )
        )
    assert annotation is None


async def test_reprocessing_run_resumes_with_a_bounded_keyset_cursor() -> None:
    async with session_factory() as db, db.begin():
        articles = [
            Article(
                original_url=f"https://example.test/{uuid.uuid4()}",
                normalized_url=f"https://example.test/{uuid.uuid4()}",
                title=f"Reprocessing article {number}",
                normalized_title_hash=uuid.uuid4().hex,
            )
            for number in range(2)
        ]
        db.add_all(articles)
        await db.flush()
        selected_ids = [article.id for article in articles]

    run_id = await create_reprocessing_run(
        processor_names=("language",), selection={"article_ids": [str(i) for i in selected_ids]}
    )
    assert await scan_reprocessing(run_id, batch_size=1) == 1
    assert await scan_reprocessing(run_id, batch_size=1) == 1
    assert await scan_reprocessing(run_id, batch_size=1) == 0

    async with session_factory() as db:
        jobs = list(
            (
                await db.scalars(
                    select(NlpJob).where(
                        NlpJob.article_id.in_(selected_ids),
                        NlpJob.processor_name == "language",
                    )
                )
            ).all()
        )
    assert len(jobs) == 2


async def test_stop_word_revision_requeues_keywords_without_touching_other_processors() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Energy markets and policy changes in a detailed English report",
            normalized_title_hash=uuid.uuid4().hex,
        )
        db.add(article)
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("language", "keywords"))
        current = await current_stop_words(db)
        unique_word = "revision" + "".join(
            chr(ord("a") + byte % 26) for byte in uuid.uuid4().bytes
        )
        await update_stop_words(
            db,
            language="en",
            current_revision=current.revision,
            words=[*current.words, unique_word],
        )
        assert (
            await request_article_nlp(db, article.id, processor_names=("keywords",)) == 1
        )
        article_id = article.id

    async with session_factory() as db:
        states = list(
            (
                await db.scalars(
                    select(ArticleNlpState).where(ArticleNlpState.article_id == article_id)
                )
            ).all()
        )
    generations = {state.processor_name: state.requested_generation for state in states}
    assert generations == {"language": 1, "keywords": 2}
