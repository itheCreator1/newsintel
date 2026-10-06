import os
import uuid
from datetime import UTC, datetime

import pytest

from app.db.session import session_factory
from app.feeds.models import Article
from app.nlp.models import (
    ArticleEntity,
    ArticleKeyword,
    ArticleNlpState,
    Entity,
    Keyword,
    NlpJob,
    NlpProcessorRun,
)
from app.nlp.routes import _lookup

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

DIGEST = "a" * 64


async def _run(db, article_id: uuid.UUID, processor: str) -> NlpProcessorRun:  # type: ignore[no-untyped-def]
    state = ArticleNlpState(
        article_id=article_id,
        processor_name=processor,
        input_fingerprint=DIGEST,
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(state)
    await db.flush()
    job = NlpJob(
        state_id=state.id,
        article_id=article_id,
        processor_name=processor,
        generation=1,
        input_fingerprint=DIGEST,
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(job)
    await db.flush()
    run = NlpProcessorRun(
        job_id=job.id,
        article_id=article_id,
        processor_name=processor,
        processor_version="test",
        algorithm_version="test",
        configuration_fingerprint=DIGEST,
        input_fingerprint=DIGEST,
        generation=1,
        outcome="success",
    )
    db.add(run)
    await db.flush()
    return run


async def test_lookup_lists_only_annotations_an_article_currently_has() -> None:
    prefix = f"lookup-{uuid.uuid4().hex}"
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Lookup evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            first_discovered_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
        )
        entities = {
            name: Entity(
                language="en",
                entity_type="ORG",
                normalized_text=f"{prefix}-{name}",
                display_text=name,
            )
            for name in ("current", "superseded", "unlinked")
        }
        keywords = {
            name: Keyword(
                language="en",
                kind="keyword",
                normalized_text=f"{prefix}-{name}",
                display_text=name,
            )
            for name in ("current", "superseded", "unlinked")
        }
        db.add_all([article, *entities.values(), *keywords.values()])
        await db.flush()
        entity_run = await _run(db, article.id, "entities")
        keyword_run = await _run(db, article.id, "keywords")
        for name, is_current in (("current", True), ("superseded", False)):
            db.add(
                ArticleEntity(
                    article_id=article.id,
                    entity_id=entities[name].id,
                    run_id=entity_run.id,
                    occurrence_count=1,
                    relevance=1,
                    occurrences=[{"start": 0, "end": 5}],
                    input_fingerprint=DIGEST,
                    is_current=is_current,
                )
            )
            db.add(
                ArticleKeyword(
                    article_id=article.id,
                    keyword_id=keywords[name].id,
                    run_id=keyword_run.id,
                    occurrence_count=1,
                    raw_score=0.1,
                    relevance=1,
                    occurrences=[{"start": 0, "end": 5}],
                    input_fingerprint=DIGEST,
                    is_current=is_current,
                )
            )

    async with session_factory() as db:
        entity_page = await _lookup(db, Entity, q=prefix, cursor=None, limit=20)
        keyword_page = await _lookup(db, Keyword, q=prefix, cursor=None, limit=20)

    assert [item.text for item in entity_page.items] == ["current"]
    assert [item.text for item in keyword_page.items] == ["current"]
