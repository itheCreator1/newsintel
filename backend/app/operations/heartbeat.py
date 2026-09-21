import time
from typing import Any

from app.operations.schemas import State

TTL_SECONDS = 24 * 3600
STALE_AFTER_SECONDS = 60


def _key(name: str) -> str:
    return f"newsintel:heartbeat:{name}"


async def beat(redis: Any, name: str) -> None:
    """Record that `name` is alive. The value is the time, so a dead process shows how long ago."""
    await redis.set(_key(name), str(time.time()), ex=TTL_SECONDS)


def beat_sync(client: Any, name: str) -> None:
    """`beat` for the synchronous Redis client a Dramatiq worker holds."""
    client.set(_key(name), str(time.time()), ex=TTL_SECONDS)


async def age_seconds(redis: Any, name: str) -> float | None:
    value = await redis.get(_key(name))
    return None if value is None else max(0.0, time.time() - float(value))


def scheduler_state(age: float | None) -> tuple[State, str]:
    if age is None:
        return "unknown", "No heartbeat recorded: the scheduler has not started, or Redis was reset"
    if age <= STALE_AFTER_SECONDS:
        return "ok", f"Last cycle {int(age)} s ago"
    return "down", f"No cycle for {int(age)} s (a cycle runs about every 10 s)"


def workers_state(ages: dict[str, float | None]) -> tuple[State, str]:
    """Liveness per queue: each worker process beats for the queues it consumes."""
    if all(age is None for age in ages.values()):
        return "unknown", "No worker heartbeat recorded: no worker has started, or Redis was reset"
    stale = [
        f"{queue} ({'never' if age is None else f'{int(age)} s ago'})"
        for queue, age in ages.items()
        if age is None or age > STALE_AFTER_SECONDS
    ]
    if stale:
        return "down", "No live worker on " + ", ".join(stale)
    return "ok", f"A live worker consumes each of the {len(ages)} queues"
