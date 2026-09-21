import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.operations import heartbeat
from app.operations.schemas import Probe, QueueDepth, State
from app.search.elasticsearch import ElasticsearchAdapter

PROBE_TIMEOUT = 2.0
# Every queue a Dramatiq actor declares (see app/jobs); a test keeps this in step with the broker.
QUEUES = ("default", "search", "monitors", "nlp", "clustering", "events")

Check = Callable[[], Awaitable[tuple[State, str | None]]]


async def probe(name: str, check: Callable[[], Awaitable[tuple[State, str | None]]],
                seconds: float = PROBE_TIMEOUT) -> Probe:  # fmt: skip
    """Run one check under a hard timeout. A failure is a result, never an exception; the detail
    names the error class only, because messages can carry connection strings."""
    started = time.monotonic()
    checked_at = datetime.now(UTC)
    try:
        state, detail = await asyncio.wait_for(check(), seconds)
    except TimeoutError:
        return Probe(
            name=name, state="down", detail=f"timed out after {seconds:g} s", checked_at=checked_at
        )
    except Exception as exc:
        return Probe(name=name, state="down", detail=type(exc).__name__, checked_at=checked_at)
    latency = int((time.monotonic() - started) * 1000)
    return Probe(name=name, state=state, latency_ms=latency, detail=detail, checked_at=checked_at)


async def run_all(checks: list[tuple[str, Check]], seconds: float = PROBE_TIMEOUT) -> list[Probe]:
    return list(await asyncio.gather(*(probe(name, check, seconds) for name, check in checks)))


def postgres_check(db: AsyncSession) -> Check:
    async def check() -> tuple[State, str | None]:
        await db.execute(text("SELECT 1"))
        return "ok", None

    return check


def redis_check(redis: Any) -> Check:
    async def check() -> tuple[State, str | None]:
        await redis.ping()
        return "ok", None

    return check


def elasticsearch_check(adapter: ElasticsearchAdapter) -> Check:
    async def check() -> tuple[State, str | None]:
        status = (await adapter.health()).get("status")
        # A single node with replicas is yellow for good, so only red (a missing primary) degrades.
        return ("degraded" if status == "red" else "ok"), f"cluster status {status}"

    return check


def nlp_check(settings: Settings) -> Check:
    async def check() -> tuple[State, str | None]:
        from app.nlp.routes import _capabilities

        found = _capabilities(settings)
        broken = [c.name for c in found if c.state == "configuration_failure"]
        if broken:
            return "degraded", f"{', '.join(broken)}: configuration failure"
        off = [c.name for c in found if c.state == "disabled"]
        return (
            "ok",
            f"{', '.join(off)} disabled by configuration" if off else "all processors available",
        )

    return check


def scheduler_check(redis: Any) -> Check:
    async def check() -> tuple[State, str | None]:
        return heartbeat.scheduler_state(await heartbeat.age_seconds(redis, "scheduler"))

    return check


async def queue_depths(redis: Any) -> list[QueueDepth]:
    """Ready, delayed and dead-lettered message counts per Dramatiq queue (Redis keys documented
    in dramatiq's dispatch.lua: a list, a `.DQ` list and a `.XQ` sorted set)."""
    async with redis.pipeline(transaction=False) as pipe:
        for queue in QUEUES:
            pipe.llen(f"dramatiq:{queue}")
            pipe.llen(f"dramatiq:{queue}.DQ")
            pipe.zcard(f"dramatiq:{queue}.XQ")
        counts = await pipe.execute()
    return [
        QueueDepth(
            queue=queue,
            ready=int(counts[i * 3]),
            delayed=int(counts[i * 3 + 1]),
            dead=int(counts[i * 3 + 2]),
        )
        for i, queue in enumerate(QUEUES)
    ]
