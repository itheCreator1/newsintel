import os

import pytest
from sqlalchemy import func, select

from app.db.session import session_factory
from app.nlp.models import Entity

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _country(db, normalized_text: str) -> Entity:  # type: ignore[no-untyped-def]
    entity = await db.scalar(
        select(Entity).where(
            Entity.language == "en",
            Entity.entity_type == "GPE",
            Entity.normalized_text == normalized_text,
        )
    )
    assert entity is not None, normalized_text
    return entity


async def test_seed_countries_links_every_spelling_to_its_country() -> None:
    from app.nlp.authority import seed_countries

    async with session_factory() as db, db.begin():
        await seed_countries(db)

    async with session_factory() as db:
        root = await _country(db, "united states")
        assert root.authority_id is None
        for spelling in ("us", "usa", "america", "united states of america"):
            variant = await _country(db, spelling)
            assert variant.authority_id == root.id, spelling
        uk = await _country(db, "uk")
        assert uk.authority_id == (await _country(db, "united kingdom")).id
        # Names the country matcher leaves alone are not tied to one country.
        georgia = await db.scalar(
            select(Entity).where(Entity.entity_type == "GPE", Entity.normalized_text == "georgia")
        )
        assert georgia is None or georgia.authority_id is None


async def test_seed_countries_is_idempotent() -> None:
    from app.nlp.authority import seed_countries

    async with session_factory() as db, db.begin():
        await seed_countries(db)
    async with session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(Entity))
        linked_before = await db.scalar(
            select(func.count()).select_from(Entity).where(Entity.authority_id.is_not(None))
        )

    async with session_factory() as db, db.begin():
        report = await seed_countries(db)

    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Entity)) == before
        assert (
            await db.scalar(
                select(func.count()).select_from(Entity).where(Entity.authority_id.is_not(None))
            )
            == linked_before
        )
    assert report.created == 0
    assert report.linked == 0
    assert report.already_linked == linked_before
