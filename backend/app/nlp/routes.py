import base64
import binascii
import importlib.metadata
import importlib.util
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import decode_cursor, encode_cursor
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
from app.nlp.reprocessing import reprocessing_status
from app.nlp.schemas import (
    AnnotationLookupItem,
    AnnotationLookupPage,
    ArticleAnnotationsResponse,
    CapabilityResponse,
    CountryAnnotationResponse,
    EntityAnnotationResponse,
    KeywordAnnotationResponse,
    LanguageAnnotationResponse,
    NlpFailurePage,
    NlpFailureResponse,
    NlpMutationResponse,
    NlpStatusResponse,
    OccurrenceResponse,
    ProcessorOutcomeResponse,
    ReprocessRequest,
    StopWordsResponse,
    StopWordsUpdate,
)
from app.nlp.service import (
    PROCESSORS,
    current_stop_words,
    request_article_nlp,
    update_stop_words,
)

router = APIRouter(tags=["nlp"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]
Config = Annotated[Settings, Depends(get_settings)]


def _capabilities(settings: Settings) -> list[CapabilityResponse]:
    ner_installed = importlib.util.find_spec("spacy") is not None
    model_installed = ner_installed and importlib.util.find_spec(settings.nlp_ner_model) is not None
    if not settings.nlp_ner_enabled:
        ner_state, detail = "disabled", "Enable the optional local spaCy image to run NER"
    elif not ner_installed:
        ner_state, detail = "configuration_failure", "spaCy is enabled but not installed"
    elif not model_installed:
        ner_state, detail = (
            "configuration_failure",
            f"spaCy model {settings.nlp_ner_model!r} is not installed",
        )
    else:
        ner_state, detail = "available", settings.nlp_ner_model
    return [
        CapabilityResponse(
            name="language",
            state="available",
            version=importlib.metadata.version("lingua-language-detector"),
        ),
        CapabilityResponse(
            name="keywords", state="available", version=importlib.metadata.version("yake")
        ),
        CapabilityResponse(name="countries", state="available", version="iso-3166-reviewed-1"),
        CapabilityResponse(
            name="entities",
            state=ner_state,
            version=importlib.metadata.version("spacy") if ner_installed else None,
            detail=detail,
        ),
    ]


def _occurrences(values: list[dict[str, object]]) -> list[OccurrenceResponse]:
    return [OccurrenceResponse.model_validate(value) for value in values]


@router.get("/articles/{article_id}/annotations", response_model=ArticleAnnotationsResponse)
async def article_annotations(
    article_id: uuid.UUID, db: Db, _auth: Auth, settings: Config
) -> ArticleAnnotationsResponse:
    if await db.get(Article, article_id) is None:
        raise HTTPException(404, "Article not found")
    states = list(
        (
            await db.scalars(
                select(ArticleNlpState)
                .where(ArticleNlpState.article_id == article_id)
                .order_by(ArticleNlpState.processor_name)
            )
        ).all()
    )
    runs: dict[str, NlpProcessorRun] = {}
    for state in states:
        run = await db.scalar(
            select(NlpProcessorRun)
            .where(
                NlpProcessorRun.article_id == article_id,
                NlpProcessorRun.processor_name == state.processor_name,
            )
            .order_by(NlpProcessorRun.started_at.desc())
            .limit(1)
        )
        if run:
            runs[state.processor_name] = run
    language = await db.scalar(
        select(ArticleLanguageAnnotation).where(
            ArticleLanguageAnnotation.article_id == article_id,
            ArticleLanguageAnnotation.is_current.is_(True),
        )
    )
    keyword_rows = (
        await db.execute(
            select(ArticleKeyword, Keyword)
            .join(Keyword)
            .where(
                ArticleKeyword.article_id == article_id,
                ArticleKeyword.is_current.is_(True),
            )
            .order_by(ArticleKeyword.relevance.desc(), Keyword.normalized_text)
            .limit(50)
        )
    ).all()
    entity_rows = (
        await db.execute(
            select(ArticleEntity, Entity)
            .join(Entity)
            .where(
                ArticleEntity.article_id == article_id,
                ArticleEntity.is_current.is_(True),
            )
            .order_by(ArticleEntity.relevance.desc(), Entity.normalized_text)
            .limit(50)
        )
    ).all()
    countries = list(
        (
            await db.scalars(
                select(ArticleCountryAnnotation)
                .where(
                    ArticleCountryAnnotation.article_id == article_id,
                    ArticleCountryAnnotation.is_current.is_(True),
                )
                .order_by(
                    ArticleCountryAnnotation.role,
                    ArticleCountryAnnotation.country_code,
                )
                .limit(50)
            )
        ).all()
    )
    source_countries = sorted(
        {
            value
            for value in (
                await db.scalars(
                    select(Feed.source_country)
                    .join(FeedArticle, FeedArticle.feed_id == Feed.id)
                    .where(
                        FeedArticle.article_id == article_id,
                        Feed.source_country.is_not(None),
                    )
                )
            ).all()
            if value
        }
    )
    return ArticleAnnotationsResponse(
        article_id=article_id,
        source_countries=source_countries,
        language=(
            LanguageAnnotationResponse(
                language=language.language,
                confidence=language.confidence,
                margin=language.margin,
                fresh=language.is_current,
            )
            if language
            else None
        ),
        keywords=[
            KeywordAnnotationResponse(
                id=value.id,
                text=value.display_text,
                normalized_text=value.normalized_text,
                kind=value.kind,
                occurrence_count=association.occurrence_count,
                raw_score=association.raw_score,
                relevance=association.relevance,
                occurrences=_occurrences(association.occurrences),
                fresh=association.is_current,
            )
            for association, value in keyword_rows
        ],
        entities=[
            EntityAnnotationResponse(
                id=value.id,
                text=value.display_text,
                normalized_text=value.normalized_text,
                entity_type=value.entity_type,
                original_label=association.original_label,
                occurrence_count=association.occurrence_count,
                relevance=association.relevance,
                occurrences=_occurrences(association.occurrences),
                fresh=association.is_current,
            )
            for association, value in entity_rows
        ],
        countries=[
            CountryAnnotationResponse(
                country_code=value.country_code,
                role=value.role,
                inferred=value.inferred,
                rule_version=value.rule_version,
                occurrence_count=value.occurrence_count,
                occurrences=_occurrences(value.occurrences),
                fresh=value.is_current,
            )
            for value in countries
        ],
        processors=[
            ProcessorOutcomeResponse(
                processor=state.processor_name,
                status=state.status,
                requested_generation=state.requested_generation,
                completed_generation=state.completed_generation,
                processor_version=state.processor_version,
                configuration_fingerprint=state.configuration_fingerprint,
                input_fingerprint=state.input_fingerprint,
                algorithm_version=runs[state.processor_name].algorithm_version
                if state.processor_name in runs
                else None,
                model_version=runs[state.processor_name].model_version
                if state.processor_name in runs
                else None,
                detail=runs[state.processor_name].detail if state.processor_name in runs else None,
                completed_at=runs[state.processor_name].completed_at
                if state.processor_name in runs
                else None,
            )
            for state in states
        ],
        capabilities=_capabilities(settings),
    )


@router.get("/nlp/status", response_model=NlpStatusResponse)
async def nlp_status(db: Db, _auth: Auth, settings: Config) -> NlpStatusResponse:
    counts = {
        str(name): int(count)
        for name, count in (
            await db.execute(select(NlpJob.status, func.count()).group_by(NlpJob.status))
        ).all()
    }
    return NlpStatusResponse(
        **{name: counts.get(name, 0) for name in ("queued", "running", "retrying", "failed")},
        capabilities=_capabilities(settings),
        reprocessing=await reprocessing_status(),
    )


@router.get("/nlp/failures", response_model=NlpFailurePage)
async def nlp_failures(
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> NlpFailurePage:
    query = (
        select(NlpJob)
        .where(NlpJob.status == "failed")
        .order_by(NlpJob.created_at.desc(), NlpJob.id.desc())
    )
    if cursor:
        created, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                NlpJob.created_at < created, and_(NlpJob.created_at == created, NlpJob.id < item_id)
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = (
        encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return NlpFailurePage(
        items=[
            NlpFailureResponse(
                id=row.id,
                article_id=row.article_id,
                processor=row.processor_name,
                attempt_count=row.attempt_count,
                error_category=row.error_category,
                error_message=row.error_message,
                created_at=row.created_at,
            )
            for row in rows[:limit]
        ],
        next_cursor=next_cursor,
    )


def _validate_processors(values: list[str]) -> tuple[str, ...]:
    processors = tuple(dict.fromkeys(values))
    if not processors or set(processors) - set(PROCESSORS):
        raise HTTPException(422, "Unknown NLP processor")
    return processors


@router.post(
    "/articles/{article_id}/nlp/reprocess", response_model=NlpMutationResponse, status_code=202
)
async def reprocess_article(
    article_id: uuid.UUID, payload: ReprocessRequest, db: Db, _mutation: Mutation
) -> NlpMutationResponse:
    if await db.get(Article, article_id) is None:
        raise HTTPException(404, "Article not found")
    count = await request_article_nlp(
        db, article_id, processor_names=_validate_processors(payload.processors), force=True
    )
    await db.commit()
    return NlpMutationResponse(status="queued", jobs_created=count)


@router.post("/nlp/jobs/{job_id}/retry", response_model=NlpMutationResponse, status_code=202)
async def retry_nlp_job(job_id: uuid.UUID, db: Db, _mutation: Mutation) -> NlpMutationResponse:
    job = await db.get(NlpJob, job_id)
    if job is None or job.status != "failed":
        raise HTTPException(404, "Failed NLP job not found")
    count = await request_article_nlp(
        db, job.article_id, processor_names=(job.processor_name,), force=True
    )
    await db.commit()
    return NlpMutationResponse(status="queued", jobs_created=count)


@router.get("/nlp/stop-words", response_model=StopWordsResponse)
async def get_stop_words(db: Db, _auth: Auth) -> StopWordsResponse:
    value = await current_stop_words(db)
    await db.commit()
    return StopWordsResponse.model_validate(value, from_attributes=True)


@router.put("/nlp/stop-words", response_model=StopWordsResponse)
async def put_stop_words(
    payload: StopWordsUpdate, db: Db, _mutation: Mutation
) -> StopWordsResponse:
    try:
        value = await update_stop_words(
            db, language="en", current_revision=payload.current_revision, words=payload.words
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return StopWordsResponse.model_validate(value, from_attributes=True)


def _lookup_cursor(value: str) -> tuple[str, uuid.UUID]:
    try:
        text_value, raw_id = base64.urlsafe_b64decode(value).decode().split("|", 1)
        return text_value, uuid.UUID(raw_id)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(422, "Invalid annotation lookup cursor") from exc


async def _lookup(
    db: AsyncSession,
    model: Any,
    *,
    q: str,
    cursor: str | None,
    limit: int,
) -> AnnotationLookupPage:
    query = select(model).order_by(model.normalized_text, model.id)
    if q.strip():
        query = query.where(model.normalized_text.startswith(q.strip().casefold()))
    if cursor:
        text_value, item_id = _lookup_cursor(cursor)
        query = query.where(
            or_(
                model.normalized_text > text_value,
                and_(model.normalized_text == text_value, model.id > item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            f"{last.normalized_text}|{last.id}".encode()
        ).decode()
    return AnnotationLookupPage(
        items=[
            AnnotationLookupItem(
                id=row.id,
                text=row.display_text,
                normalized_text=row.normalized_text,
                kind=row.entity_type if isinstance(row, Entity) else row.kind,
            )
            for row in rows[:limit]
        ],
        next_cursor=next_cursor,
    )


@router.get("/nlp/entities", response_model=AnnotationLookupPage)
async def entity_lookup(
    db: Db,
    _auth: Auth,
    q: str = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AnnotationLookupPage:
    return await _lookup(db, Entity, q=q, cursor=cursor, limit=limit)


@router.get("/nlp/keywords", response_model=AnnotationLookupPage)
async def keyword_lookup(
    db: Db,
    _auth: Auth,
    q: str = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AnnotationLookupPage:
    return await _lookup(db, Keyword, q=q, cursor=cursor, limit=limit)
