import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.feeds.models import Article
from app.nlp.input import InputDocument, build_input_document
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleKeyword,
    ArticleLanguageAnnotation,
    ArticleNlpState,
    NlpJob,
    StopWordRevision,
)
from app.search.service import request_indexing

PROCESSORS = ("language", "keywords", "entities", "countries")
PROCESSOR_VERSIONS = {name: "1" for name in PROCESSORS}


def next_retry_at(now: datetime, attempt: int) -> datetime:
    return now + timedelta(seconds=min(900, 30 * (2 ** max(0, attempt - 1))))


def configuration_fingerprint(
    processor_name: str, *, stop_words_fingerprint: str | None = None
) -> str:
    settings = get_settings()
    data = {
        "processor": processor_name,
        "version": PROCESSOR_VERSIONS[processor_name],
        "stop_words": stop_words_fingerprint if processor_name == "keywords" else None,
        "ner_enabled": settings.nlp_ner_enabled if processor_name == "entities" else None,
        "ner_model": settings.nlp_ner_model if processor_name == "entities" else None,
        "max_input_characters": settings.nlp_max_input_characters,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _default_stop_words() -> list[str]:
    path = Path(__file__).with_name("data") / "stopwords-en.txt"
    return sorted(
        line.strip().casefold()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )


def stop_words_fingerprint(words: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(set(words))).encode()).hexdigest()


async def current_stop_words(db: AsyncSession, language: str = "en") -> StopWordRevision:
    await db.execute(text("SELECT pg_advisory_xact_lock(728341905)"))
    revision = await db.scalar(
        select(StopWordRevision)
        .where(StopWordRevision.language == language)
        .order_by(StopWordRevision.revision.desc())
        .limit(1)
    )
    if revision is None:
        words = _default_stop_words()
        revision = StopWordRevision(
            language=language,
            revision=1,
            words=words,
            configuration_fingerprint=stop_words_fingerprint(words),
        )
        db.add(revision)
        await db.flush()
    return revision


async def update_stop_words(
    db: AsyncSession, *, language: str, current_revision: int, words: list[str]
) -> StopWordRevision:
    current = await current_stop_words(db, language)
    if current.revision != current_revision:
        raise ValueError("stop-word revision has changed; reload before saving")
    normalized = sorted(
        {word.strip().casefold() for word in words if word.strip() and word.strip().isalpha()}
    )
    revision = StopWordRevision(
        language=language,
        revision=current.revision + 1,
        words=normalized,
        configuration_fingerprint=stop_words_fingerprint(normalized),
    )
    db.add(revision)
    await db.flush()
    return revision


async def load_input_document(db: AsyncSession, article_id: uuid.UUID) -> InputDocument:
    article = await db.scalar(
        select(Article)
        .where(Article.id == article_id)
        .options(selectinload(Article.content), selectinload(Article.discoveries))
    )
    if article is None:
        raise LookupError("article not found")
    descriptions = [
        (item.discovered_at, item.id, item.description)
        for item in article.discoveries
        if item.description
    ]
    return build_input_document(
        title=article.title,
        body=article.content.text if article.content else None,
        descriptions=descriptions,
    )


async def request_article_nlp(
    db: AsyncSession,
    article_id: uuid.UUID,
    *,
    processor_names: tuple[str, ...] = PROCESSORS,
    force: bool = False,
) -> int:
    await db.scalar(select(Article.id).where(Article.id == article_id).with_for_update())
    document = await load_input_document(db, article_id)
    stop_words = await current_stop_words(db)
    requested = 0
    for name in processor_names:
        if name not in PROCESSOR_VERSIONS:
            raise ValueError(f"unknown NLP processor: {name}")
        version = PROCESSOR_VERSIONS[name]
        config = configuration_fingerprint(
            name, stop_words_fingerprint=stop_words.configuration_fingerprint
        )
        state = await db.scalar(
            select(ArticleNlpState)
            .where(
                ArticleNlpState.article_id == article_id,
                ArticleNlpState.processor_name == name,
            )
            .with_for_update()
        )
        if (
            state is not None
            and not force
            and state.input_fingerprint == document.fingerprint
            and state.processor_version == version
            and state.configuration_fingerprint == config
        ):
            continue
        if state is None:
            state = ArticleNlpState(
                article_id=article_id,
                processor_name=name,
                input_fingerprint=document.fingerprint,
                processor_version=version,
                configuration_fingerprint=config,
            )
            db.add(state)
            await db.flush()
        else:
            state.requested_generation += 1
            state.input_fingerprint = document.fingerprint
            state.processor_version = version
            state.configuration_fingerprint = config
            state.status = "queued"
            await db.execute(
                update(NlpJob)
                .where(
                    NlpJob.state_id == state.id,
                    NlpJob.status.in_(("queued", "running", "retrying")),
                )
                .values(status="superseded", claim_token=None, claim_expires_at=None)
            )
        db.add(
            NlpJob(
                state_id=state.id,
                article_id=article_id,
                processor_name=name,
                generation=state.requested_generation,
                input_fingerprint=document.fingerprint,
                processor_version=version,
                configuration_fingerprint=config,
            )
        )
        requested += 1
        if name == "keywords":
            await db.execute(
                update(ArticleKeyword)
                .where(ArticleKeyword.article_id == article_id)
                .values(is_current=False)
            )
        elif name == "entities":
            await db.execute(
                update(ArticleEntity)
                .where(ArticleEntity.article_id == article_id)
                .values(is_current=False)
            )
        elif name == "countries":
            await db.execute(
                update(ArticleCountryAnnotation)
                .where(ArticleCountryAnnotation.article_id == article_id)
                .values(is_current=False)
            )
        elif name == "language":
            await db.execute(
                update(ArticleLanguageAnnotation)
                .where(ArticleLanguageAnnotation.article_id == article_id)
                .values(is_current=False)
            )
    if requested:
        await request_indexing(db, article_id)
    return requested


def job_due(now: datetime):  # type: ignore[no-untyped-def]
    return (
        NlpJob.status.in_(("queued", "running", "retrying"))
        & (NlpJob.next_attempt_at <= now)
        & or_(NlpJob.claim_expires_at.is_(None), NlpJob.claim_expires_at <= now)
    )


async def claim_job(
    db: AsyncSession, job_id: uuid.UUID, lease_seconds: int
) -> tuple[NlpJob, str] | None:
    now = datetime.now(UTC)
    job = await db.scalar(select(NlpJob).where(NlpJob.id == job_id).with_for_update())
    if (
        job is None
        or job.status not in ("queued", "running", "retrying")
        or job.next_attempt_at > now
        or (job.claim_expires_at is not None and job.claim_expires_at > now)
    ):
        return None
    state = await db.get(ArticleNlpState, job.state_id, with_for_update=True)
    if state is None or state.requested_generation != job.generation:
        job.status = "superseded"
        await db.commit()
        return None
    token = uuid.uuid4().hex
    job.claim_token = token
    job.claim_expires_at = now + timedelta(seconds=lease_seconds)
    job.status = "running"
    job.attempt_count += 1
    state.status = "running"
    await db.commit()
    return job, token
