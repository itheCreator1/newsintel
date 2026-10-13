"""The baseline migration builds exactly the schema the 31 migrations it replaced built.

tests/snapshots/schema_0031.txt is that schema, read from a database the old chain (0001 to
0031, still in git history before the squash) had upgraded. Each test here builds its own empty
database, so nothing the rest of the suite writes can change what is compared.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

BACKEND = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(__file__).parent / "snapshots" / "schema_0031.txt"

# Every query returns one text column; the snapshot is their rows, sorted. Functions, sequences and
# types that an extension owns (pg_trgm puts its functions in public) belong to the extension line.
_NOT_FROM_AN_EXTENSION = (
    "NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = {oid} AND d.deptype = 'e')"
)
SNAPSHOT_QUERIES = [
    "SELECT 'extension ' || extname FROM pg_extension WHERE extname <> 'plpgsql'",
    """
    SELECT format('column %s.%s %s not_null=%s default=%s identity=%s generated=%s',
                  c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                  pg_get_expr(ad.adbin, ad.adrelid), a.attidentity, a.attgenerated)
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
    WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
      AND a.attnum > 0 AND NOT a.attisdropped AND c.relname <> 'alembic_version'
    """,
    """
    SELECT format('constraint %s %s %s', conrelid::regclass, conname, pg_get_constraintdef(oid))
    FROM pg_constraint
    -- NOT NULL is in the column lines; PostgreSQL 18 also lists it here as a constraint.
    WHERE connamespace = 'public'::regnamespace AND conrelid <> 0 AND contype <> 'n'
      AND conrelid <> 'alembic_version'::regclass
    """,
    """
    SELECT format('index %s %s', indexname, indexdef) FROM pg_indexes
    WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    """,
    """
    SELECT format('sequence %s %s start=%s increment=%s', c.relname, s.seqtypid::regtype,
                  s.seqstart, s.seqincrement)
    FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
    WHERE c.relnamespace = 'public'::regnamespace
    """,
    f"""
    SELECT format('type %s %s %s', t.typname, t.typtype,
                  (SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
                   FROM pg_enum e WHERE e.enumtypid = t.oid))
    FROM pg_type t
    WHERE t.typnamespace = 'public'::regnamespace AND t.typtype IN ('e', 'd')
      AND {_NOT_FROM_AN_EXTENSION.format(oid="t.oid")}
    """,
    f"""
    SELECT format('function %s', pg_get_functiondef(p.oid)) FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace
      AND {_NOT_FROM_AN_EXTENSION.format(oid="p.oid")}
    """,
    """
    SELECT format('trigger %s %s', tgname, pg_get_triggerdef(oid)) FROM pg_trigger
    WHERE NOT tgisinternal
    """,
    "SELECT format('view %s %s', viewname, definition) FROM pg_views WHERE schemaname = 'public'",
]


async def schema_snapshot(connection: AsyncConnection) -> list[str]:
    lines: list[str] = []
    for query in SNAPSHOT_QUERIES:
        lines += (await connection.execute(text(query))).scalars().all()
    # The rows a migration writes (the Wikidata throttle's single row) are part of what it builds.
    tables = (
        await connection.execute(
            text(
                "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace"
                " AND relkind = 'r' AND relname <> 'alembic_version'"
            )
        )
    ).scalars()
    for table in tables:
        rows = await connection.execute(
            text(f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY 1')
        )
        lines += [f"row {table} {row}" for row in rows.scalars()]
    return sorted(lines)


def _alembic(database_url: str, *args: str) -> None:
    environment = os.environ | {"NEWSINTEL_DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=environment, check=True
    )


@pytest_asyncio.fixture(loop_scope="session")
async def empty_database() -> AsyncIterator[str]:
    url = make_url(os.environ["NEWSINTEL_DATABASE_URL"])
    name = f"{url.database}_baseline_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def _snapshot_of(database_url: str) -> list[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await schema_snapshot(connection)
    finally:
        await engine.dispose()


async def test_baseline_matches_the_old_chain(empty_database: str) -> None:
    await asyncio.to_thread(_alembic, empty_database, "upgrade", "0001")
    built = await _snapshot_of(empty_database)
    expected = SNAPSHOT.read_text().splitlines()
    missing = sorted(set(expected) - set(built))
    extra = sorted(set(built) - set(expected))
    assert not missing and not extra, f"missing: {missing}\nextra: {extra}"


async def test_baseline_downgrade_leaves_an_empty_schema(empty_database: str) -> None:
    await asyncio.to_thread(_alembic, empty_database, "upgrade", "0001")
    await asyncio.to_thread(_alembic, empty_database, "downgrade", "base")
    left = [
        line for line in await _snapshot_of(empty_database) if not line.startswith("extension ")
    ]
    assert left == []
