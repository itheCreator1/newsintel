import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.nlp.execution import process_job
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleLanguageAnnotation,
    ArticleNlpState,
    NlpJob,
    NlpProcessorRun,
)
from app.nlp.reprocessing import count_selection, create_reprocessing_run, scan_reprocessing
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


async def test_country_job_claim_and_publication_are_durable() -> None:
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="France announces energy policy for international markets",
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
                description="Officials in France described the policy. Germany replied.",
                metadata_json={},
            )
        )
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("countries",))
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
        annotations = list(
            (
                await db.scalars(
                    select(ArticleCountryAnnotation).where(
                        ArticleCountryAnnotation.article_id == article.id,
                        ArticleCountryAnnotation.is_current.is_(True),
                    )
                )
            ).all()
        )
    assert job is not None and job.status == "succeeded"
    assert {annotation.country_code for annotation in annotations} == {"FR", "DE"}

    # A rule_version this long is only possible after the 0009 migration widened the
    # column; leaving it behind would break test_clustering_postgres's downgrade-to-0007
    # round trip, which passes through 0009's downgrade on a shared test database.
    async with session_factory() as db, db.begin():
        feed_article = await db.scalar(
            select(FeedArticle).where(FeedArticle.article_id == article.id)
        )
        assert feed_article is not None
        await db.delete(feed_article)
        await db.flush()
        stored_article = await db.get(Article, article.id)
        assert stored_article is not None
        await db.delete(stored_article)


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


async def test_reprocessing_can_select_only_articles_in_one_language() -> None:
    async with session_factory() as db, db.begin():
        articles = [
            Article(
                original_url=f"https://example.test/{uuid.uuid4()}",
                normalized_url=f"https://example.test/{uuid.uuid4()}",
                title=f"Language selection article {number}",
                normalized_title_hash=uuid.uuid4().hex,
            )
            for number in range(3)
        ]
        db.add_all(articles)
        await db.flush()
        jobs: dict[uuid.UUID, NlpJob] = {}
        for article in articles:
            state = ArticleNlpState(
                article_id=article.id,
                processor_name="language",
                input_fingerprint="a" * 64,
                processor_version="test",
                configuration_fingerprint="a" * 64,
            )
            db.add(state)
            await db.flush()
            jobs[article.id] = NlpJob(
                state_id=state.id,
                article_id=article.id,
                processor_name="language",
                generation=1,
                input_fingerprint="a" * 64,
                processor_version="test",
                configuration_fingerprint="a" * 64,
            )
            db.add(jobs[article.id])
        await db.flush()
        # Greek now, Greek only in the past, English now.
        for article, language, is_current in (
            (articles[0], "el", True),
            (articles[1], "el", False),
            (articles[1], "en", True),
            (articles[2], "en", True),
        ):
            run = NlpProcessorRun(
                job_id=jobs[article.id].id,
                article_id=article.id,
                processor_name="language",
                processor_version="test",
                algorithm_version="test",
                configuration_fingerprint="a" * 64,
                input_fingerprint="a" * 64,
                generation=1,
                outcome="success",
            )
            db.add(run)
            await db.flush()
            db.add(
                ArticleLanguageAnnotation(
                    article_id=article.id,
                    run_id=run.id,
                    language=language,
                    confidence=0.99,
                    margin=0.9,
                    input_fingerprint="a" * 64,
                    is_current=is_current,
                )
            )
    selection: dict[str, object] = {
        "article_ids": [str(article.id) for article in articles],
        "language": "el",
    }

    assert await count_selection(selection) == 1
    run_id = await create_reprocessing_run(processor_names=("entities",), selection=selection)
    assert await scan_reprocessing(run_id) == 1
    async with session_factory() as db:
        queued = set(
            (
                await db.scalars(
                    select(NlpJob.article_id).where(
                        NlpJob.article_id.in_([article.id for article in articles]),
                        NlpJob.processor_name == "entities",
                    )
                )
            ).all()
        )
    assert queued == {articles[0].id}


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
        unique_word = "revision" + "".join(chr(ord("a") + byte % 26) for byte in uuid.uuid4().bytes)
        await update_stop_words(
            db,
            language="en",
            current_revision=current.revision,
            words=[*current.words, unique_word],
        )
        assert await request_article_nlp(db, article.id, processor_names=("keywords",)) == 1
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


async def test_greek_switch_queues_the_greek_archive_once_and_refuses_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from app.core.config import Settings
    from app.nlp import routes
    from app.nlp.models import NlpLanguageSetting, NlpReprocessingRun
    from app.nlp.reprocessing import scan_active_reprocessing
    from app.nlp.schemas import GreekEntitiesUpdate
    from app.nlp.service import greek_ner_enabled

    settings = Settings(nlp_ner_enabled=True)
    monkeypatch.setattr(routes, "_greek_unavailable", lambda _: "model missing")
    async with session_factory() as db:
        with pytest.raises(HTTPException) as refused:
            await routes.put_greek_entities(GreekEntitiesUpdate(enabled=True), db, None, settings)  # type: ignore[arg-type]
        assert refused.value.status_code == 409
        assert not await greek_ner_enabled(db)

    monkeypatch.setattr(routes, "_greek_unavailable", lambda _: None)
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Ο Τσίπρας μίλησε στην Αθήνα",
            normalized_title_hash=uuid.uuid4().hex,
        )
        db.add(article)
        await db.flush()
        state = ArticleNlpState(
            article_id=article.id,
            processor_name="language",
            input_fingerprint="a" * 64,
            processor_version="test",
            configuration_fingerprint="a" * 64,
        )
        db.add(state)
        await db.flush()
        job = NlpJob(
            state_id=state.id,
            article_id=article.id,
            processor_name="language",
            generation=1,
            input_fingerprint="a" * 64,
            processor_version="test",
            configuration_fingerprint="a" * 64,
        )
        db.add(job)
        await db.flush()
        run = NlpProcessorRun(
            job_id=job.id,
            article_id=article.id,
            processor_name="language",
            processor_version="test",
            algorithm_version="test",
            configuration_fingerprint="a" * 64,
            input_fingerprint="a" * 64,
            generation=1,
            outcome="success",
        )
        db.add(run)
        await db.flush()
        db.add(
            ArticleLanguageAnnotation(
                article_id=article.id,
                run_id=run.id,
                language="el",
                confidence=0.99,
                margin=0.9,
                input_fingerprint="a" * 64,
            )
        )

    async with session_factory() as db:
        on = await routes.put_greek_entities(GreekEntitiesUpdate(enabled=True), db, None, settings)  # type: ignore[arg-type]
        again = await routes.put_greek_entities(
            GreekEntitiesUpdate(enabled=True), db, None, settings
        )  # type: ignore[arg-type]
    assert on.enabled and on.available and on.reprocessing_run_id is not None
    assert on.queued_article_count == on.greek_article_count >= 1
    # Already on: nothing is queued a second time.
    assert again.reprocessing_run_id is None

    while await scan_active_reprocessing():
        pass
    async with session_factory() as db:
        greek_run = await db.get(NlpReprocessingRun, on.reprocessing_run_id)
        entity_jobs = (
            await db.scalars(
                select(NlpJob).where(
                    NlpJob.article_id == article.id, NlpJob.processor_name == "entities"
                )
            )
        ).all()
        off = await routes.put_greek_entities(
            GreekEntitiesUpdate(enabled=False), db, None, settings
        )  # type: ignore[arg-type]
        setting = await db.get(NlpLanguageSetting, "el")
    assert greek_run is not None and greek_run.status == "succeeded"
    assert len(entity_jobs) == 1
    assert not off.enabled and off.reprocessing_run_id is None
    assert setting is not None and not setting.ner_enabled


async def _run_entities_job(
    monkeypatch: pytest.MonkeyPatch,
    *,
    title: str,
    description: str,
    spans: list[tuple[str, str]],
    greek: bool = False,
) -> tuple[uuid.UUID, uuid.UUID, list[str]]:
    """Run one whole entities job for a new article; returns (job id, article id, loaded models).

    spaCy is faked (the test image has none; the ner-model gate stage runs the real model), but
    language detection, the model choice, the name keys and the upsert are all real.
    """
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.nlp.models import NlpLanguageSetting

    loaded_models: list[str] = []

    class FakeSpan:
        def __init__(self, value: str, full_text: str, label: str) -> None:
            self.text = value
            self.label_ = label
            self.start_char = full_text.index(value)
            self.end_char = self.start_char + len(value)

    class FakePipeline:
        meta = {"version": "test"}

        def __call__(self, value: str) -> SimpleNamespace:
            return SimpleNamespace(ents=[FakeSpan(span, value, label) for span, label in spans])

    def load(model: str, **_: object) -> FakePipeline:
        loaded_models.append(model)
        return FakePipeline()

    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr(
        "app.nlp.processors.importlib.import_module", lambda _: SimpleNamespace(load=load)
    )
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})
    settings = Settings(nlp_ner_enabled=True)
    monkeypatch.setattr("app.nlp.execution.get_settings", lambda: settings)
    monkeypatch.setattr("app.nlp.service.get_settings", lambda: settings)

    async with session_factory() as db, db.begin():
        if greek:
            await db.merge(NlpLanguageSetting(language="el", ner_enabled=True))
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title=title,
            normalized_title_hash=uuid.uuid4().hex,
        )
        feed = Feed(
            name="Entities job source",
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
                feed_title=title,
                feed_url=article.original_url,
                description=description,
                metadata_json={},
            )
        )
        await db.flush()
        await request_article_nlp(db, article.id, processor_names=("entities",))
        await db.flush()
        job = await db.scalar(select(NlpJob).where(NlpJob.article_id == article.id))
        assert job is not None
        job_id = job.id
        article_id = article.id

    try:
        async with session_factory() as db:
            claimed = await claim_job(db, job_id, lease_seconds=60)
        assert claimed is not None
        await process_job(job_id, claimed[1])
        async with session_factory() as db:
            finished = await db.get(NlpJob, job_id)
        assert finished is not None and finished.status == "succeeded"
    finally:
        if greek:
            async with session_factory() as db, db.begin():
                await db.merge(NlpLanguageSetting(language="el", ner_enabled=False))
    return job_id, article_id, loaded_models


async def _current_entity_rows(article_id: uuid.UUID) -> list[tuple[str, str, str]]:
    from app.nlp.models import ArticleEntity, Entity

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Entity.language, Entity.entity_type, Entity.normalized_text)
                .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
                .where(
                    ArticleEntity.article_id == article_id,
                    ArticleEntity.is_current.is_(True),
                )
            )
        ).all()
    return [tuple(row) for row in rows]


async def test_greek_article_entities_are_stored_with_language_el(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Greek article goes through a whole entities job, from detection to the database."""
    _, article_id, loaded_models = await _run_entities_job(
        monkeypatch,
        title="Ο Αλέξης Τσίπρας μίλησε στην Αθήνα για την οικονομία και την ενέργεια",
        description=(
            "Ο Τσίπρας είπε ότι η κυβέρνηση πρέπει να στηρίξει τα νοικοκυριά, "
            "ενώ ο ΣΥΡΙΖΑ ζήτησε νέα μέτρα για τις τιμές της ενέργειας στην Ελλάδα."
        ),
        spans=[("Αλέξης Τσίπρας", "PERSON"), ("Τσίπρας", "PERSON"), ("Αθήνα", "GPE")],
        greek=True,
    )

    assert loaded_models == ["el_core_news_sm"]
    # "Τσίπρας" folds into the one full name, and the key drops accents and the final "ς".
    assert set(await _current_entity_rows(article_id)) == {
        ("el", "PERSON", "αλεξη τσιπρα"),
        ("el", "GPE", "αθηνα"),
    }


async def test_one_current_row_per_article_and_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model tagging one country GPE once and LOC once still gives the article one row."""
    from app.core.config import get_settings
    from app.nlp.routes import article_annotations

    _, article_id, _ = await _run_entities_job(
        monkeypatch,
        title="US officials met European ministers about energy prices and supply this week",
        description=(
            "The meeting in the U.S. capital ended without an agreement on energy prices, "
            "officials said after several hours of talks about winter supply."
        ),
        spans=[("US", "GPE"), ("U.S.", "LOC")],
    )
    # The route reports the real spaCy install; drop the fake one before asking it.
    monkeypatch.undo()

    assert await _current_entity_rows(article_id) == [("en", "GPE", "united states")]
    async with session_factory() as db:
        annotations = await article_annotations(article_id, db, None, get_settings())  # type: ignore[arg-type]
    listed = [(item.entity_type, item.normalized_text) for item in annotations.entities]
    assert listed == [("GPE", "united states")]
    assert annotations.entities[0].occurrence_count == 2


async def test_downgrade_to_0016_removes_only_the_language_settings() -> None:
    import asyncio

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import func, text

    from app.nlp.models import NlpLanguageSetting

    async with session_factory() as db, db.begin():
        await db.merge(NlpLanguageSetting(language="el", ner_enabled=False))
    async with session_factory() as db:
        jobs_before = await db.scalar(select(func.count()).select_from(NlpJob))

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0016")
    try:
        async with session_factory() as db:
            assert (
                await db.scalar(text("SELECT to_regclass('nlp_language_settings') IS NULL")) is True
            )
            assert await db.scalar(select(func.count()).select_from(NlpJob)) == jobs_before
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

    async with session_factory() as db:
        # Back at head the table exists again, empty: Greek entities are off until turned on.
        assert await db.scalar(select(func.count()).select_from(NlpLanguageSetting)) == 0


ENGLISH_DESCRIPTION = (
    "The minister said the government would publish the plan next week, after talks with "
    "unions and employers about wages, prices and the cost of energy for households."
)


async def _entity_row(article_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
    from app.nlp.models import ArticleEntity

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(ArticleEntity.entity_id, ArticleEntity.observed_entity_id).where(
                    ArticleEntity.article_id == article_id,
                    ArticleEntity.is_current.is_(True),
                )
            )
        ).all()
    return [tuple(row) for row in rows]


async def test_ner_writes_the_root_and_keeps_the_observed_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.nlp.models import Entity

    token = uuid.uuid4().hex[:8]
    async with session_factory() as db, db.begin():
        root = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=f"orla brennik {token}",
            display_text=f"Orla Brennik {token}",
        )
        db.add(root)
        await db.flush()
        variant = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=f"o. brennik {token}",
            display_text=f"O. Brennik {token}",
            authority_id=root.id,
        )
        db.add(variant)
        await db.flush()
        root_id, variant_id = root.id, variant.id

    _, article_id, _ = await _run_entities_job(
        monkeypatch,
        title=f"O. Brennik {token} announced a new wage plan for public sector workers today",
        description=ENGLISH_DESCRIPTION,
        spans=[(f"O. Brennik {token}", "PERSON")],
    )

    # Search, the graph and the dossier count the root; the variant the article used is kept.
    assert await _entity_row(article_id) == [(root_id, variant_id)]


async def test_ner_leaves_observed_empty_for_a_root(monkeypatch: pytest.MonkeyPatch) -> None:
    token = uuid.uuid4().hex[:8]
    _, article_id, _ = await _run_entities_job(
        monkeypatch,
        title=f"Ada Quill {token} announced a new wage plan for public sector workers today",
        description=ENGLISH_DESCRIPTION,
        spans=[(f"Ada Quill {token}", "PERSON")],
    )

    rows = await _entity_row(article_id)
    assert len(rows) == 1 and rows[0][1] is None


async def test_ner_never_overwrites_a_preferred_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.nlp.models import Entity

    token = uuid.uuid4().hex[:8]
    async with session_factory() as db, db.begin():
        entity = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=f"mara voss {token}",
            display_text=f"Mara Voss {token}",
            preferred_text=f"Voss, Mara {token}",
        )
        db.add(entity)
        await db.flush()
        entity_id = entity.id

    await _run_entities_job(
        monkeypatch,
        title=f"MARA VOSS {token} announced a new wage plan for public sector workers today",
        description=ENGLISH_DESCRIPTION,
        spans=[(f"MARA VOSS {token}", "PERSON")],
    )

    async with session_factory() as db:
        stored = await db.get(Entity, entity_id)
    assert stored is not None
    # The article's spelling still updates the display text; the chosen name stays.
    assert stored.display_text == f"MARA VOSS {token}"
    assert stored.preferred_text == f"Voss, Mara {token}"


async def test_ambiguous_surname_is_not_folded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.nlp.models import Entity

    surname = f"Kestrelwick{uuid.uuid4().hex[:6]}"
    async with session_factory() as db, db.begin():
        db.add(
            Entity(
                language="en",
                entity_type="PERSON",
                normalized_text=surname.casefold(),
                display_text=surname,
                ambiguous=True,
            )
        )

    _, article_id, _ = await _run_entities_job(
        monkeypatch,
        title=f"Ivo {surname} announced a new wage plan for public sector workers today",
        description=f"{surname} said so. " + ENGLISH_DESCRIPTION,
        spans=[(f"Ivo {surname}", "PERSON"), (surname, "PERSON")],
    )

    # Marked ambiguous, the surname alone stays its own entity instead of joining "Ivo ...".
    assert {key for _, _, key in await _current_entity_rows(article_id)} == {
        f"ivo {surname.casefold()}",
        surname.casefold(),
    }
