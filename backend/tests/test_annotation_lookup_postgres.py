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
from app.search.criteria import _resolve_annotations

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


async def test_entity_lookup_finds_a_country_by_its_other_spellings() -> None:
    # Its own language keeps the row clear of other tests' "united states".
    language = f"t{uuid.uuid4().hex[:8]}"
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Country lookup evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            first_discovered_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
        )
        country = Entity(
            language=language,
            entity_type="GPE",
            normalized_text="united states",
            display_text="United States",
        )
        db.add_all([article, country])
        await db.flush()
        run = await _run(db, article.id, "entities")
        db.add(
            ArticleEntity(
                article_id=article.id,
                entity_id=country.id,
                run_id=run.id,
                occurrence_count=1,
                relevance=1,
                occurrences=[{"start": 0, "end": 2}],
                input_fingerprint=DIGEST,
                is_current=True,
            )
        )

    async def found(model, q: str) -> set[uuid.UUID]:  # type: ignore[no-untyped-def]
        ids: set[uuid.UUID] = set()
        cursor = None
        async with session_factory() as db:
            while True:
                page = await _lookup(db, model, q=q, cursor=cursor, limit=50)
                ids.update(item.id for item in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    return ids

    for q in ("US", "U.S.", "the us", "USA", "Amer", "united"):
        assert country.id in await found(Entity, q), q
    assert country.id not in await found(Entity, "France")
    assert country.id not in await found(Keyword, "US")


async def test_entity_lookup_lists_a_matching_country_first_across_pages() -> None:
    language = f"t{uuid.uuid4().hex[:8]}"
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Country ranking evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            first_discovered_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
        )
        entities = [
            Entity(
                language=language,
                entity_type=entity_type,
                normalized_text=name.casefold(),
                display_text=name,
            )
            for entity_type, name in (
                ("ORG", "U.S. Bombers"),
                ("ORG", "U.S. Navy"),
                # Not the country: only the GPE row is the one the spellings were folded into.
                ("ORG", "United States"),
                ("GPE", "United States"),
            )
        ]
        db.add_all([article, *entities])
        await db.flush()
        run = await _run(db, article.id, "entities")
        db.add_all(
            ArticleEntity(
                article_id=article.id,
                entity_id=entity.id,
                run_id=run.id,
                occurrence_count=1,
                relevance=1,
                occurrences=[{"start": 0, "end": 2}],
                input_fingerprint=DIGEST,
                is_current=True,
            )
            for entity in entities
        )
    bombers, navy, organisation, country = (entity.id for entity in entities)

    async def walk(q: str) -> list[uuid.UUID]:
        # Other tests' rows share the table; a page of one puts a cursor between every pair.
        ids: list[uuid.UUID] = []
        cursor = None
        async with session_factory() as db:
            while True:
                page = await _lookup(db, Entity, q=q, cursor=cursor, limit=1)
                ids.extend(item.id for item in page.items if item.id in {e.id for e in entities})
                cursor = page.next_cursor
                if cursor is None:
                    return ids

    # A half-typed "U.S." keeps the country, ahead of the names that merely start that way.
    for q in ("U.S.", "u.s"):
        assert await walk(q) == [country, bombers, navy], q
    assert await walk("the U.S") == [country]
    assert await walk("U.S. N") == [navy]
    assert await walk("united") == [country, organisation]


async def test_greek_entities_are_found_with_or_without_accents_and_endings() -> None:
    language = f"t{uuid.uuid4().hex[:8]}"
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Greek lookup evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            first_discovered_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
        )
        # Stored as the NER processor stores Greek names: under greek_name_key.
        person = Entity(
            language=language,
            entity_type="PERSON",
            normalized_text="κουτσουμπα",
            display_text="Κουτσούμπας",
        )
        db.add_all([article, person])
        await db.flush()
        run = await _run(db, article.id, "entities")
        db.add(
            ArticleEntity(
                article_id=article.id,
                entity_id=person.id,
                run_id=run.id,
                occurrence_count=1,
                relevance=1,
                occurrences=[{"start": 0, "end": 11}],
                input_fingerprint=DIGEST,
                is_current=True,
            )
        )

    async with session_factory() as db:
        for q in ("Κουτσούμπας", "ΚΟΥΤΣΟΥΜΠΑΣ", "Κουτσούμπα", "κουτσου", "Κου"):
            page = await _lookup(db, Entity, q=q, cursor=None, limit=50)
            assert person.id in {item.id for item in page.items}, q
        resolved = await _resolve_annotations(db, ["Κουτσούμπα", "ΚΟΥΤΣΟΥΜΠΑΣ"], Entity)
    assert resolved == [str(person.id)]


async def _stored_entity(normalized_text: str, display_text: str, entity_type: str) -> Entity:
    # Its own language keeps the row apart from other tests' entities of the same name.
    language = f"t{uuid.uuid4().hex[:8]}"
    async with session_factory() as db, db.begin():
        entity = Entity(
            language=language,
            entity_type=entity_type,
            normalized_text=normalized_text,
            display_text=display_text,
        )
        db.add(entity)
    return entity


async def test_entity_search_finds_a_country_by_its_other_spellings() -> None:
    country = await _stored_entity("united states", "United States", "GPE")

    async with session_factory() as db:
        for value in ("US", "U.S.", "USA", "the United States", "America"):
            resolved = await _resolve_annotations(db, [value], Entity)
            assert str(country.id) in resolved, value


async def test_entity_search_ignores_a_leading_the_and_a_possessive() -> None:
    # Extraction stores "The Hague" under "hague" (canonical_entity); entity:"The Hague" must
    # reach it.
    hague = await _stored_entity("hague", "The Hague", "GPE")
    reuters = await _stored_entity("reuters", "Reuters", "ORG")

    async with session_factory() as db:
        for value in ("The Hague", "the hague", "Hague's", "hague"):
            assert str(hague.id) in await _resolve_annotations(db, [value], Entity), value
        assert str(reuters.id) in await _resolve_annotations(db, ["Reuters'"], Entity)
        # Keywords keep their own exact match.
        assert str(hague.id) not in await _resolve_annotations(db, ["The Hague"], Keyword)
