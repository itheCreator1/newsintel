import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.articles.enrichment import lock_article, mark_enriched
from app.clustering.service import request_clustering
from app.core.config import get_settings
from app.db.session import session_factory
from app.entities.models import Entity, EntityAuthorityChange, EntityNameClaim
from app.nlp.authority import RowPart, combine_rows
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleKeyword,
    ArticleLanguageAnnotation,
    ArticleNlpState,
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
from app.nlp.service import (
    ANNOTATION_LANGUAGES,
    current_stop_words,
    greek_ner_enabled,
    load_input_document,
    next_retry_at,
)
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
        settings = get_settings()
        greek_model = settings.nlp_ner_model_el if await greek_ner_enabled(db) else None
        initial_context = ProcessorContext(
            text=document.text,
            input_fingerprint=document.fingerprint,
            sections=document.sections,
            language=None,
            stop_words=frozenset(),
            ner_enabled=settings.nlp_ner_enabled,
            ner_model=settings.nlp_ner_model,
            ner_model_el=greek_model,
        )
        validate_input_size(initial_context, settings.nlp_max_input_characters)
        language = None
        if job.processor_name != "language":
            language = detect_language(initial_context).language
        ambiguous: frozenset[str] = frozenset()
        if job.processor_name == "entities" and language is not None:
            ambiguous = frozenset(
                (
                    await db.scalars(
                        select(Entity.normalized_text).where(
                            Entity.ambiguous.is_(True),
                            Entity.language == language,
                            Entity.entity_type == "PERSON",
                        )
                    )
                ).all()
            )
        # Keywords use the stop words of the article's own language (English for any other).
        stop_words = await current_stop_words(
            db, language if language in ANNOTATION_LANGUAGES else "en"
        )
        context = ProcessorContext(
            text=document.text,
            input_fingerprint=document.fingerprint,
            sections=document.sections,
            language=language,
            stop_words=frozenset(stop_words.words),
            ner_enabled=settings.nlp_ner_enabled,
            ner_model=settings.nlp_ner_model,
            ner_model_el=greek_model,
            ambiguous_names=ambiguous,
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


async def _publish(loaded: LoadedJob, token: str, result: ProcessorResult) -> bool:  # noqa: C901
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        # The article first, then the job: the order request_article_nlp takes them in.
        await lock_article(db, loaded.article_id)
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
                        language=loaded.context.language or "en",
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
            # Two names of one root are one row, as an authority merge run leaves them.
            parts: dict[uuid.UUID, list[RowPart]] = {}
            for entity_value in result.entities:
                # preferred_text is the user's and never written here.
                language = loaded.context.language or "en"
                observed_id, authority_id, created = (
                    await db.execute(
                        insert(Entity)
                        .values(
                            # Entities are extracted only for languages with a model ("en", "el").
                            language=language,
                            entity_type=entity_value.entity_type,
                            normalized_text=entity_value.normalized_text,
                            display_text=entity_value.text,
                        )
                        .on_conflict_do_update(
                            constraint="uq_nlp_entity_identity",
                            set_={"display_text": entity_value.text},
                        )
                        # xmax is 0 on a row this statement inserted, not on one it updated.
                        .returning(Entity.id, Entity.authority_id, literal_column("xmax = 0"))
                    )
                ).one()
                if created and authority_id is None:
                    authority_id = await _claimed_by(
                        db, observed_id, language, entity_value.normalized_text
                    )
                # A known variant is counted under its root; the row keeps which variant the
                # article used.
                parts.setdefault(authority_id or observed_id, []).append(
                    RowPart(
                        observed_id if authority_id else None,
                        entity_value.original_label,
                        [asdict(item) for item in entity_value.occurrences],
                        entity_value.occurrence_count,
                    )
                )
            for entity_id, entity_parts in parts.items():
                combined = combine_rows(entity_id, entity_parts)
                db.add(
                    ArticleEntity(
                        article_id=job.article_id,
                        entity_id=entity_id,
                        observed_entity_id=combined.observed_entity_id,
                        run_id=run.id,
                        original_label=combined.original_label,
                        occurrence_count=combined.occurrence_count,
                        relevance=combined.relevance,
                        occurrences=combined.occurrences,
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
        if job.processor_name == "entities":
            # Entities feed candidate selection, so clustering follows their publication.
            await request_clustering(db, job.article_id)
        await mark_enriched(db, job.article_id, now)
        await request_indexing(db, job.article_id)
    return True


async def _claimed_by(
    db: AsyncSession, entity_id: uuid.UUID, language: str, normalized_text: str
) -> uuid.UUID | None:
    """A name new to the archive whose every type a root takes joins that root at once."""
    root_id = await db.scalar(
        select(EntityNameClaim.root_id).where(
            EntityNameClaim.language == language,
            EntityNameClaim.normalized_text == normalized_text,
        )
    )
    if root_id is None:
        return None
    await db.execute(update(Entity).where(Entity.id == entity_id).values(authority_id=root_id))
    db.add(
        EntityAuthorityChange(
            action="merged",
            entity_id=entity_id,
            other_id=root_id,
            before={"authority_id": None},
            after={"authority_id": str(root_id), "by": "claim"},
            created_at=func.clock_timestamp(),
        )
    )
    return root_id


async def _record_failure(job_id: uuid.UUID, token: str, exc: Exception) -> None:
    now = datetime.now(UTC)
    permanent = isinstance(exc, (ConfigurationError, InputTooLarge))
    category = "configuration" if isinstance(exc, ConfigurationError) else "input_too_large"
    if not permanent:
        category = "processor"
    async with session_factory() as db, db.begin():
        article_id = await db.scalar(select(NlpJob.article_id).where(NlpJob.id == job_id))
        if article_id is not None:
            await lock_article(db, article_id)
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
            error_category=category,
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
        # A step that failed for good is finished: the article may be complete without it.
        if job.status == "failed" and await mark_enriched(db, job.article_id, now):
            await request_indexing(db, job.article_id)


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
