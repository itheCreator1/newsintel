import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.session import session_factory
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleKeyword,
    ArticleLanguageAnnotation,
    ArticleNlpState,
    Entity,
    Keyword,
    NlpJob,
    NlpProcessorRun,
)
from app.nlp.processors import (
    ConfigurationError,
    CountryResult,
    EntityResult,
    InputTooLarge,
    KeywordResult,
    LanguageResult,
    ProcessorContext,
    detect_countries,
    detect_language,
    extract_entities,
    extract_keywords,
    validate_input_size,
)
from app.nlp.service import current_stop_words, load_input_document, next_retry_at
from app.search.service import request_indexing

type ProcessorResult = LanguageResult | KeywordResult | EntityResult | CountryResult


@dataclass(frozen=True)
class LoadedJob:
    job_id: uuid.UUID
    state_id: uuid.UUID
    article_id: uuid.UUID
    processor_name: str
    generation: int
    processor_version: str
    configuration_fingerprint: str
    input_fingerprint: str
    context: ProcessorContext


async def _load_job(job_id: uuid.UUID, token: str) -> LoadedJob | None:
    async with session_factory() as db, db.begin():
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        job = await db.get(NlpJob, job_id)
        if (
            job is None
            or job.claim_token != token
            or job.claim_expires_at is None
            or job.claim_expires_at <= datetime.now(UTC)
        ):
            return None
        state = await db.get(ArticleNlpState, job.state_id)
        if (
            state is None
            or state.requested_generation != job.generation
            or state.input_fingerprint != job.input_fingerprint
            or state.processor_version != job.processor_version
            or state.configuration_fingerprint != job.configuration_fingerprint
        ):
            return None
        document = await load_input_document(db, job.article_id)
        if document.fingerprint != job.input_fingerprint:
            return None
        stop_words = await current_stop_words(db)
        settings = get_settings()
        initial_context = ProcessorContext(
            text=document.text,
            input_fingerprint=document.fingerprint,
            sections=document.sections,
            language=None,
            stop_words=frozenset(stop_words.words),
            ner_enabled=settings.nlp_ner_enabled,
            ner_model=settings.nlp_ner_model,
        )
        validate_input_size(initial_context, settings.nlp_max_input_characters)
        language = None
        if job.processor_name != "language":
            language = detect_language(initial_context).language
        context = ProcessorContext(
            text=document.text,
            input_fingerprint=document.fingerprint,
            sections=document.sections,
            language=language,
            stop_words=frozenset(stop_words.words),
            ner_enabled=settings.nlp_ner_enabled,
            ner_model=settings.nlp_ner_model,
        )
        return LoadedJob(
            job.id,
            state.id,
            job.article_id,
            job.processor_name,
            job.generation,
            job.processor_version,
            job.configuration_fingerprint,
            job.input_fingerprint,
            context,
        )


def _run_processor(loaded: LoadedJob) -> ProcessorResult:
    if loaded.processor_name == "language":
        return detect_language(loaded.context)
    if loaded.processor_name == "keywords":
        return extract_keywords(loaded.context)
    if loaded.processor_name == "entities":
        return extract_entities(loaded.context)
    if loaded.processor_name == "countries":
        return detect_countries(loaded.context)
    raise ConfigurationError(f"unknown NLP processor: {loaded.processor_name}")


async def _renew_lease(job_id: uuid.UUID, token: str) -> bool:
    settings = get_settings()
    async with session_factory() as db, db.begin():
        job = await db.scalar(select(NlpJob).where(NlpJob.id == job_id).with_for_update())
        if job is None or job.claim_token != token or job.status != "running":
            return False
        if job.claim_expires_at is None or job.claim_expires_at <= datetime.now(UTC):
            return False
        job.claim_expires_at = datetime.now(UTC) + timedelta(seconds=settings.nlp_lease_seconds)
        return True


async def _heartbeat(job_id: uuid.UUID, token: str, finished: asyncio.Event) -> None:
    interval = max(1.0, min(30.0, get_settings().nlp_lease_seconds / 3))
    while True:
        try:
            await asyncio.wait_for(finished.wait(), timeout=interval)
            return
        except TimeoutError:
            if not await _renew_lease(job_id, token):
                return


def _algorithm(result: ProcessorResult) -> str:
    return result.algorithm_version


def _model_version(result: ProcessorResult) -> str | None:
    return result.model_version if isinstance(result, EntityResult) else None


async def _publish(loaded: LoadedJob, token: str, result: ProcessorResult) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        job = await db.scalar(select(NlpJob).where(NlpJob.id == loaded.job_id).with_for_update())
        state = await db.get(ArticleNlpState, loaded.state_id, with_for_update=True)
        if (
            job is None
            or state is None
            or job.claim_token != token
            or job.claim_expires_at is None
            or job.claim_expires_at <= now
            or state.requested_generation != loaded.generation
            or state.input_fingerprint != loaded.input_fingerprint
            or state.processor_version != loaded.processor_version
            or state.configuration_fingerprint != loaded.configuration_fingerprint
        ):
            return False
        detail = None
        if isinstance(result, LanguageResult):
            detail = json.dumps(
                {
                    "language": result.language,
                    "confidence": result.confidence,
                    "margin": result.margin,
                },
                sort_keys=True,
            )
        run = NlpProcessorRun(
            job_id=job.id,
            article_id=job.article_id,
            processor_name=job.processor_name,
            processor_version=job.processor_version,
            algorithm_version=_algorithm(result),
            model_version=_model_version(result),
            configuration_fingerprint=job.configuration_fingerprint,
            input_fingerprint=job.input_fingerprint,
            generation=job.generation,
            outcome=result.outcome,
            detail=detail,
            completed_at=now,
        )
        db.add(run)
        await db.flush()
        if isinstance(result, LanguageResult):
            await db.execute(
                update(ArticleLanguageAnnotation)
                .where(
                    ArticleLanguageAnnotation.article_id == job.article_id,
                    ArticleLanguageAnnotation.is_current.is_(True),
                )
                .values(is_current=False)
            )
            db.add(
                ArticleLanguageAnnotation(
                    article_id=job.article_id,
                    run_id=run.id,
                    language=result.language,
                    confidence=result.confidence,
                    margin=result.margin,
                    input_fingerprint=job.input_fingerprint,
                )
            )
        elif isinstance(result, KeywordResult):
            await db.execute(
                update(ArticleKeyword)
                .where(
                    ArticleKeyword.article_id == job.article_id,
                    ArticleKeyword.is_current.is_(True),
                )
                .values(is_current=False)
            )
            for keyword_value in result.keywords:
                keyword_id = await db.scalar(
                    insert(Keyword)
                    .values(
                        language="en",
                        kind=keyword_value.kind,
                        normalized_text=keyword_value.normalized_text,
                        display_text=keyword_value.text,
                    )
                    .on_conflict_do_update(
                        constraint="uq_nlp_keyword_identity",
                        set_={"display_text": keyword_value.text},
                    )
                    .returning(Keyword.id)
                )
                db.add(
                    ArticleKeyword(
                        article_id=job.article_id,
                        keyword_id=keyword_id,
                        run_id=run.id,
                        occurrence_count=keyword_value.occurrence_count,
                        raw_score=keyword_value.raw_score,
                        relevance=keyword_value.relevance,
                        occurrences=[asdict(item) for item in keyword_value.occurrences],
                        input_fingerprint=job.input_fingerprint,
                    )
                )
        elif isinstance(result, EntityResult):
            await db.execute(
                update(ArticleEntity)
                .where(
                    ArticleEntity.article_id == job.article_id,
                    ArticleEntity.is_current.is_(True),
                )
                .values(is_current=False)
            )
            for entity_value in result.entities:
                entity_id = await db.scalar(
                    insert(Entity)
                    .values(
                        language="en",
                        entity_type=entity_value.entity_type,
                        normalized_text=entity_value.normalized_text,
                        display_text=entity_value.text,
                    )
                    .on_conflict_do_update(
                        constraint="uq_nlp_entity_identity",
                        set_={"display_text": entity_value.text},
                    )
                    .returning(Entity.id)
                )
                db.add(
                    ArticleEntity(
                        article_id=job.article_id,
                        entity_id=entity_id,
                        run_id=run.id,
                        original_label=entity_value.original_label,
                        occurrence_count=entity_value.occurrence_count,
                        relevance=entity_value.relevance,
                        occurrences=[asdict(item) for item in entity_value.occurrences],
                        input_fingerprint=job.input_fingerprint,
                    )
                )
        else:
            await db.execute(
                update(ArticleCountryAnnotation)
                .where(
                    ArticleCountryAnnotation.article_id == job.article_id,
                    ArticleCountryAnnotation.is_current.is_(True),
                )
                .values(is_current=False)
            )
            for country_value in result.mentioned:
                db.add(
                    ArticleCountryAnnotation(
                        article_id=job.article_id,
                        run_id=run.id,
                        country_code=country_value.country_code,
                        role="mentioned",
                        inferred=False,
                        rule_version=result.algorithm_version,
                        occurrence_count=country_value.occurrence_count,
                        occurrences=[asdict(item) for item in country_value.occurrences],
                        input_fingerprint=job.input_fingerprint,
                    )
                )
            if result.primary is not None:
                primary_value = result.primary
                db.add(
                    ArticleCountryAnnotation(
                        article_id=job.article_id,
                        run_id=run.id,
                        country_code=primary_value.country_code,
                        role="primary",
                        inferred=True,
                        rule_version=result.algorithm_version,
                        occurrence_count=primary_value.occurrence_count,
                        occurrences=[asdict(item) for item in primary_value.occurrences],
                        input_fingerprint=job.input_fingerprint,
                    )
                )
        job.status = "succeeded"
        job.claim_token = None
        job.claim_expires_at = None
        job.error_category = None
        job.error_message = None
        job.completed_at = now
        state.completed_generation = job.generation
        state.status = result.outcome
        await request_indexing(db, job.article_id)
    return True


async def _record_failure(job_id: uuid.UUID, token: str, exc: Exception) -> None:
    now = datetime.now(UTC)
    permanent = isinstance(exc, (ConfigurationError, InputTooLarge))
    category = "configuration" if isinstance(exc, ConfigurationError) else "input_too_large"
    if not permanent:
        category = "processor"
    async with session_factory() as db, db.begin():
        job = await db.scalar(select(NlpJob).where(NlpJob.id == job_id).with_for_update())
        if (
            job is None
            or job.claim_token != token
            or job.claim_expires_at is None
            or job.claim_expires_at <= now
        ):
            return
        state = await db.get(ArticleNlpState, job.state_id, with_for_update=True)
        run = NlpProcessorRun(
            job_id=job.id,
            article_id=job.article_id,
            processor_name=job.processor_name,
            processor_version=job.processor_version,
            algorithm_version=f"{job.processor_name}-failed",
            model_version=None,
            configuration_fingerprint=job.configuration_fingerprint,
            input_fingerprint=job.input_fingerprint,
            generation=job.generation,
            outcome="failed",
            detail=str(exc)[:1000],
            completed_at=now,
        )
        db.add(run)
        job.claim_token = None
        job.claim_expires_at = None
        job.error_category = category
        job.error_message = str(exc)[:1000]
        if permanent or job.attempt_count >= 5:
            job.status = "failed"
        else:
            job.status = "retrying"
            job.next_attempt_at = next_retry_at(now, job.attempt_count)
        if state is not None:
            state.status = job.status


async def process_job(job_id: uuid.UUID, token: str) -> None:
    finished = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(job_id, token, finished))
    try:
        loaded = await _load_job(job_id, token)
        if loaded is None:
            return
        result = await asyncio.to_thread(_run_processor, loaded)
        await _publish(loaded, token, result)
    except Exception as exc:
        await _record_failure(job_id, token, exc)
    finally:
        finished.set()
        await heartbeat
