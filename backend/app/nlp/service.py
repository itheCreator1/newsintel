import hashlib
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.feeds.models import Article
from app.nlp.input import InputDocument, build_input_document
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleKeyword,
    ArticleNlpState,
    NlpJob,
)

PROCESSORS = ("language", "keywords", "entities", "countries")
PROCESSOR_VERSIONS = {name: "1" for name in PROCESSORS}


def next_retry_at(now: datetime, attempt: int) -> datetime:
    return now + timedelta(seconds=min(900, 30 * (2 ** max(0, attempt - 1))))


def configuration_fingerprint(processor_name: str) -> str:
    data = {"processor": processor_name, "version": PROCESSOR_VERSIONS[processor_name]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


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
    requested = 0
    for name in processor_names:
        if name not in PROCESSOR_VERSIONS:
            raise ValueError(f"unknown NLP processor: {name}")
        version = PROCESSOR_VERSIONS[name]
        config = configuration_fingerprint(name)
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
    return requested
