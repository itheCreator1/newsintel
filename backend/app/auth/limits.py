"""Repeated failed sign-ins hold the name for a while.

Counted per name rather than per address: behind the bundled proxy every request comes from the
same address. A held name also holds its owner out, but only for LOCKOUT, and the CLI still works.
"""

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import LoginFailure

__all__ = ["LOCKOUT", "MAX_FAILURES", "WINDOW", "LoginFailure", "SignInHeld"]

MAX_FAILURES = 10
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)


class SignInHeld(Exception):
    def __init__(self, retry_after: timedelta) -> None:
        self.retry_after = retry_after
        minutes = max(1, math.ceil(retry_after.total_seconds() / 60))
        super().__init__(
            f"Too many failed sign-ins. Try again in {minutes} minute{'' if minutes == 1 else 's'}."
        )


async def check(db: AsyncSession, username: str, now: datetime | None = None) -> None:
    """Raise SignInHeld while the name is held."""
    now = now or datetime.now(UTC)
    locked_until = await db.scalar(
        select(LoginFailure.locked_until).where(LoginFailure.username == username)
    )
    if locked_until is not None and locked_until > now:
        raise SignInHeld(locked_until - now)


async def failed(db: AsyncSession, username: str, now: datetime | None = None) -> None:
    """Count a failure; a count older than WINDOW, or one whose hold has passed, starts over."""
    now = now or datetime.now(UTC)
    # Guesses at names that do not exist leave rows too; forget the ones that no longer count.
    await db.execute(
        delete(LoginFailure).where(
            LoginFailure.username != username,
            LoginFailure.first_failed_at < now - WINDOW,
            or_(LoginFailure.locked_until.is_(None), LoginFailure.locked_until <= now),
        )
    )
    row = await db.scalar(
        select(LoginFailure).where(LoginFailure.username == username).with_for_update()
    )
    if row is None:
        await db.execute(
            insert(LoginFailure)
            .values(username=username, failures=1, first_failed_at=now)
            .on_conflict_do_nothing()
        )
    elif row.first_failed_at < now - WINDOW or (row.locked_until and row.locked_until <= now):
        row.failures, row.first_failed_at, row.locked_until = 1, now, None
    else:
        row.failures += 1
        if row.failures >= MAX_FAILURES:
            row.locked_until = now + LOCKOUT
    await db.commit()


async def succeeded(db: AsyncSession, username: str) -> None:
    await db.execute(delete(LoginFailure).where(LoginFailure.username == username))
    await db.commit()
