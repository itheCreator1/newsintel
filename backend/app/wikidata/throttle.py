"""One throttle for every request the app sends to Wikidata.

Wikimedia asks anonymous clients of the Action API for one request at a time, under 5 a second,
a pause after slow answers, and respect for 429 and Retry-After (Robot policy; Wikimedia APIs/Rate
limits: 200 a minute with a proper User-Agent). We stay far inside that: one request in flight for
the whole app, at most one every 3 s, a daily budget, and pauses that stop all traffic.

`PostgresThrottle` keeps the state in one row, locked for the length of a request, so the api,
the workers, the scheduler and the CLI share it and a pause survives a restart. `MemoryThrottle`
applies the same rules in one process, for the contract check and the unit tests.
"""

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.db.session import session_factory
from app.wikidata.errors import WikidataBudgetSpent, WikidataPaused
from app.wikidata.models import WikidataRequestCount, WikidataThrottle

Outcome = Literal["ok", "rate_limited", "blocked", "error"]
Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]

REPEAT_WINDOW = timedelta(hours=1)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ThrottleState:
    next_request_at: datetime | None = None
    paused_until: datetime | None = None
    pause_reason: str | None = None
    error_strikes: int = 0
    last_rate_limited_at: datetime | None = None
    day: date | None = None
    requests_today: int = 0


def _midnight_after(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)


def wait_seconds(state: ThrottleState, now: datetime, settings: Settings) -> float:
    """How long to wait before the next request, or why none may go out today."""
    if state.paused_until is not None and state.paused_until > now:
        raise WikidataPaused(state.paused_until, state.pause_reason or "paused")
    if state.day == now.date() and state.requests_today >= settings.wikidata_daily_request_budget:
        raise WikidataBudgetSpent(_midnight_after(now))
    if state.next_request_at is None:
        return 0.0
    return max(0.0, (state.next_request_at - now).total_seconds())


def after_request(
    state: ThrottleState,
    *,
    started: datetime,
    finished: datetime,
    outcome: Outcome,
    retry_after: float | None,
    settings: Settings,
) -> ThrottleState:
    """The state once a request has been answered (or has failed) at `finished`."""
    took = (finished - started).total_seconds()
    gap = settings.wikidata_min_interval_seconds
    if took > settings.wikidata_slow_response_seconds:
        gap = max(gap, settings.wikidata_slow_wait_seconds)
    today = finished.date()
    state = replace(
        state,
        next_request_at=finished + timedelta(seconds=gap),
        day=today,
        requests_today=(state.requests_today if state.day == today else 0) + 1,
    )
    asked = timedelta(seconds=retry_after or 0)
    if outcome == "ok":
        return replace(state, error_strikes=0)
    if outcome == "rate_limited":
        pause = max(asked, timedelta(minutes=settings.wikidata_rate_limit_pause_minutes))
        last = state.last_rate_limited_at
        if last is not None and finished - last < REPEAT_WINDOW:
            pause = max(pause, timedelta(hours=settings.wikidata_repeat_rate_limit_pause_hours))
        return replace(
            state,
            paused_until=finished + pause,
            pause_reason="rate_limited",
            last_rate_limited_at=finished,
        )
    if outcome == "blocked":
        pause = max(asked, timedelta(hours=settings.wikidata_blocked_pause_hours))
        return replace(state, paused_until=finished + pause, pause_reason="blocked")
    strikes = state.error_strikes + 1
    backoff = min(
        timedelta(minutes=settings.wikidata_error_pause_minutes) * 2 ** (strikes - 1),
        timedelta(hours=settings.wikidata_error_pause_max_hours),
    )
    return replace(
        state,
        paused_until=finished + max(asked, backoff),
        pause_reason="error",
        error_strikes=strikes,
    )


class Slot:
    """One request's turn: say how it went with `record`; an unrecorded turn counts as an error."""

    def __init__(self, clock: Clock) -> None:
        self.now = clock
        self.started = clock()
        self.outcome: Outcome | None = None
        self.retry_after: float | None = None

    def record(self, outcome: Outcome, retry_after: float | None = None) -> None:
        self.outcome = outcome
        self.retry_after = retry_after


class Throttle(Protocol):
    def slot(self, kind: str) -> AbstractAsyncContextManager[Slot]: ...


class _Base:
    def __init__(self, settings: Settings, *, clock: Clock = utc_now, sleep: Sleep = asyncio.sleep):
        self.settings = settings
        self.clock = clock
        self.sleep = sleep

    def _finish(self, state: ThrottleState, slot: Slot) -> tuple[ThrottleState, Outcome]:
        outcome: Outcome = slot.outcome or "error"
        return (
            after_request(
                state,
                started=slot.started,
                finished=self.clock(),
                outcome=outcome,
                retry_after=slot.retry_after,
                settings=self.settings,
            ),
            outcome,
        )


class MemoryThrottle(_Base):
    """The same rules for one process only."""

    def __init__(self, settings: Settings, *, clock: Clock = utc_now, sleep: Sleep = asyncio.sleep):
        super().__init__(settings, clock=clock, sleep=sleep)
        self.state = ThrottleState()
        self.counts: dict[tuple[str, str, str], int] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def slot(self, kind: str) -> AsyncIterator[Slot]:
        async with self._lock:
            wait = wait_seconds(self.state, self.clock(), self.settings)
            if wait > 0:
                await self.sleep(wait)
            slot = Slot(self.clock)
            try:
                yield slot
            finally:
                self.state, outcome = self._finish(self.state, slot)
                key = (slot.started.date().isoformat(), kind, outcome)
                self.counts[key] = self.counts.get(key, 0) + 1


class PostgresThrottle(_Base):
    """The shared throttle: its row stays locked from the wait until the answer is recorded."""

    @asynccontextmanager
    async def slot(self, kind: str) -> AsyncIterator[Slot]:
        async with session_factory() as db:
            # Committed in `finally`, so a failed request's pause is kept too.
            await db.begin()
            # The row is created on first use, and again after a test truncates the tables.
            await db.execute(insert(WikidataThrottle).values(id=1).on_conflict_do_nothing())
            row = await db.scalar(
                select(WikidataThrottle).where(WikidataThrottle.id == 1).with_for_update()
            )
            assert row is not None
            state = ThrottleState(
                next_request_at=row.next_request_at,
                paused_until=row.paused_until,
                pause_reason=row.pause_reason,
                error_strikes=row.error_strikes,
                last_rate_limited_at=row.last_rate_limited_at,
                day=row.day,
                requests_today=row.requests_today,
            )
            wait = wait_seconds(state, self.clock(), self.settings)
            if wait > 0:
                await self.sleep(wait)
            slot = Slot(self.clock)
            try:
                yield slot
            finally:
                state, outcome = self._finish(state, slot)
                row.next_request_at = state.next_request_at
                row.paused_until = state.paused_until
                row.pause_reason = state.pause_reason
                row.error_strikes = state.error_strikes
                row.last_rate_limited_at = state.last_rate_limited_at
                row.day = state.day
                row.requests_today = state.requests_today
                took_ms = max(0, math.ceil((self.clock() - slot.started).total_seconds() * 1000))
                await db.execute(
                    insert(WikidataRequestCount)
                    .values(
                        day=slot.started.date(),
                        kind=kind,
                        outcome=outcome,
                        count=1,
                        total_ms=took_ms,
                    )
                    .on_conflict_do_update(
                        index_elements=["day", "kind", "outcome"],
                        set_={
                            "count": WikidataRequestCount.count + 1,
                            "total_ms": WikidataRequestCount.total_ms + took_ms,
                        },
                    )
                )
                await db.commit()
