import asyncio
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.session import session_factory
from app.nlp.models import Entity

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

AUTHORITY_TABLES = ("entity_distinct", "entity_authority_changes", "entity_authority_runs")
AUTHORITY_COLUMNS = ("authority_id", "preferred_text", "status", "ambiguous", "note")


async def _columns(db, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    rows = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :table"),
        {"table": table},
    )
    return {row[0] for row in rows}


async def _insert_old_style_entity(db, name: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    """An entity written with only the columns that existed before 0018."""
    entity_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO nlp_entities (id, language, entity_type, normalized_text, display_text)"
            " VALUES (:id, 'en', 'PERSON', :name, :name)"
        ),
        {"id": entity_id, "name": name},
    )
    return entity_id


async def test_upgrade_adds_authority_columns_with_safe_defaults() -> None:
    async with session_factory() as db, db.begin():
        entity_id = await _insert_old_style_entity(db, f"authority-{uuid.uuid4().hex}")

    async with session_factory() as db:
        row = (
            await db.execute(
                text(
                    "SELECT authority_id, preferred_text, status, ambiguous, note"
                    " FROM nlp_entities WHERE id = :id"
                ),
                {"id": entity_id},
            )
        ).one()
        # Every existing entity is its own root, unconfirmed, with no preferred name.
        assert tuple(row) == (None, None, "provisional", False, None)
        assert "observed_entity_id" in await _columns(db, "article_nlp_entities")
        for table in AUTHORITY_TABLES:
            assert await db.scalar(text(f"SELECT to_regclass('{table}') IS NOT NULL")) is True
        # Trigram index for the duplicate suggestions of PR-5.
        index = await db.scalar(text("SELECT to_regclass('ix_nlp_entities_normalized_trgm')"))
        assert index is not None


async def test_entity_distinct_rejects_a_reversed_pair() -> None:
    async with session_factory() as db, db.begin():
        first = await _insert_old_style_entity(db, f"distinct-{uuid.uuid4().hex}")
        second = await _insert_old_style_entity(db, f"distinct-{uuid.uuid4().hex}")
    low, high = sorted([first, second], key=str)

    async with session_factory() as db:
        # Stored once, smaller id first, so (a, b) and (b, a) cannot both exist.
        with pytest.raises(IntegrityError):
            async with db.begin():
                await db.execute(
                    text("INSERT INTO entity_distinct (a_id, b_id) VALUES (:a, :b)"),
                    {"a": high, "b": low},
                )
    async with session_factory() as db, db.begin():
        await db.execute(
            text("INSERT INTO entity_distinct (a_id, b_id) VALUES (:a, :b)"),
            {"a": low, "b": high},
        )


async def test_entity_status_accepts_only_provisional_or_established() -> None:
    async with session_factory() as db, db.begin():
        entity_id = await _insert_old_style_entity(db, f"status-{uuid.uuid4().hex}")

    async with session_factory() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                await db.execute(
                    text("UPDATE nlp_entities SET status = 'final' WHERE id = :id"),
                    {"id": entity_id},
                )
    async with session_factory() as db, db.begin():
        await db.execute(
            text("UPDATE nlp_entities SET status = 'established' WHERE id = :id"),
            {"id": entity_id},
        )


async def test_downgrade_to_0017_removes_only_the_authority_schema() -> None:
    async with session_factory() as db, db.begin():
        await _insert_old_style_entity(db, f"downgrade-{uuid.uuid4().hex}")
    async with session_factory() as db:
        entities_before = await db.scalar(select(func.count()).select_from(Entity))

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0017")
    try:
        async with session_factory() as db:
            for table in AUTHORITY_TABLES:
                assert await db.scalar(text(f"SELECT to_regclass('{table}') IS NULL")) is True
            assert not set(AUTHORITY_COLUMNS) & await _columns(db, "nlp_entities")
            assert "observed_entity_id" not in await _columns(db, "article_nlp_entities")
            assert await db.scalar(text("SELECT count(*) FROM nlp_entities")) == entities_before
            assert (
                await db.scalar(text("SELECT to_regclass('nlp_language_settings') IS NOT NULL"))
                is True
            )
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

    async with session_factory() as db:
        assert set(AUTHORITY_COLUMNS) <= await _columns(db, "nlp_entities")
