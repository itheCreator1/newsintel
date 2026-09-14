from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_storage_keys(
    db: AsyncSession, keys: Iterable[str], *, shared: bool
) -> None:
    """Take transaction-scoped PostgreSQL locks in a stable order."""
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    statement = text(f"SELECT {function}(hashtextextended(:key, 0))")
    for key in sorted(set(keys)):
        await db.execute(statement, {"key": key})
