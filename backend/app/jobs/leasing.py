"""Claims, leases and retries, shared by every queue table.

A queued row (an NLP or clustering job, an article processing job, a search delivery) is worked by
one worker at a time. Claiming it hands out a random token and a lease; while the lease lasts only
the token's holder may finish the row, and a lease that runs out lets the next claim take it over.
A worker that may outlive its lease renews it with a heartbeat.

The functions change the row in memory; the caller has locked it and commits.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeGuard

from sqlalchemy import ColumnElement, or_

ACTIVE_STATUSES = ("queued", "running", "retrying")

# Stored error messages are cut to this length.
MESSAGE_CHARS = 1000


@dataclass(frozen=True)
class Retries:
    """How a queue retries: a backoff that doubles from `base_seconds` up to `cap_seconds`, and
    at most `max_attempts` attempts (None: no limit)."""

    base_seconds: float
    cap_seconds: float
    max_attempts: int | None

    def backoff(self, attempt: int, *, retry_after: float | None = None) -> timedelta:
        """The wait before the next attempt, after `attempt` attempts; a server's Retry-After
        replaces the doubling, within the same cap."""
        wanted = (
            self.base_seconds * 2 ** max(0, attempt - 1) if retry_after is None else retry_after
        )
        return timedelta(seconds=min(self.cap_seconds, max(0.0, wanted)))

    def exhausted(self, attempts: int) -> bool:
        return self.max_attempts is not None and attempts >= self.max_attempts


def leased(row: Any, now: datetime) -> bool:
    return row.claim_expires_at is not None and row.claim_expires_at > now


def claimable(row: Any, now: datetime) -> bool:
    """Active, due, and not leased to a worker (a lease that ran out frees it)."""
    return row.status in ACTIVE_STATUSES and row.next_attempt_at <= now and not leased(row, now)


def due(model: Any, now: datetime) -> ColumnElement[bool]:
    """`claimable` as a filter on a queue table, for the sweeps that find work."""
    clause: ColumnElement[bool] = (
        model.status.in_(ACTIVE_STATUSES)
        & (model.next_attempt_at <= now)
        & or_(model.claim_expires_at.is_(None), model.claim_expires_at <= now)
    )
    return clause


def claim(row: Any, now: datetime, lease_seconds: float, *, count_attempt: bool = True) -> str:
    """Lease the row to a new token and mark it running; returns the token."""
    token = uuid.uuid4().hex
    row.claim_token = token
    row.claim_expires_at = now + timedelta(seconds=lease_seconds)
    row.status = "running"
    if count_attempt:
        row.attempt_count += 1
    return token


def holds[Row](row: Row | None, token: str, now: datetime) -> TypeGuard[Row]:
    """Whether `token` still holds the row: it is the row's token and its lease has not run out."""
    return row is not None and row.claim_token == token and leased(row, now)  # type: ignore[attr-defined]


def release(row: Any) -> None:
    row.claim_token = None
    row.claim_expires_at = None


def renew(row: Any, token: str, now: datetime, lease_seconds: float) -> bool:
    """Extend a running row's lease for its holder; False when the claim is no longer theirs."""
    if row is None or row.status != "running" or not holds(row, token, now):
        return False
    row.claim_expires_at = now + timedelta(seconds=lease_seconds)
    return True


def record_failure(
    row: Any,
    now: datetime,
    category: str,
    message: str,
    retries: Retries,
    *,
    attempts: int,
    permanent: bool = False,
    retry_after: float | None = None,
) -> None:
    """Release the row after a failed attempt: it fails for good when the failure is permanent
    or `attempts` used up the queue's limit, and otherwise waits for its backoff."""
    release(row)
    row.error_category = category
    row.error_message = message[:MESSAGE_CHARS]
    if permanent or retries.exhausted(attempts):
        row.status = "failed"
        if hasattr(row, "completed_at"):
            row.completed_at = now
    else:
        row.status = "retrying"
        row.next_attempt_at = now + retries.backoff(attempts, retry_after=retry_after)


def heartbeat_interval(lease_seconds: float) -> float:
    """Three heartbeats per lease, at most every 30 s and at least 1 s apart."""
    return max(1.0, min(30.0, lease_seconds / 3))


async def heartbeat(
    renew_lease: Callable[[], Awaitable[bool]], interval: float, finished: asyncio.Event
) -> None:
    """Renew a lease every `interval` seconds until the work finishes or the claim is lost."""
    while True:
        try:
            await asyncio.wait_for(finished.wait(), timeout=interval)
            return
        except TimeoutError:
            if not await renew_lease():
                return
