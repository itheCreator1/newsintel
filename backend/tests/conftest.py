import asyncio
import os

import pytest
from sqlalchemy import text

from app.db.session import session_factory


async def _truncate_all() -> None:
    async with session_factory() as db, db.begin():
        tables = (
            await db.scalars(
                text(
                    "SELECT quote_ident(tablename) FROM pg_tables"
                    " WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        ).all()
        if tables:
            await db.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session", autouse=True)
def _clean_database() -> None:
    # Start the gated suite from empty tables, so an earlier run's rows cannot change a result.
    # Rows this run writes stay behind on purpose: CI's restore rehearsal dumps them.
    # ponytail: isolation per run, not per test; per-test rollback needs tests to take an
    # injected session instead of the global session_factory.
    if os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") == "1":
        asyncio.run(_truncate_all())
