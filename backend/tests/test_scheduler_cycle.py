import asyncio

import pytest

from app import scheduler

STEPS = (
    "schedule_due_feeds",
    "schedule_due_articles",
    "schedule_due_nlp",
    "scan_active_reprocessing",
    "schedule_due_clustering",
    "schedule_due_search",
    "schedule_due_monitors",
    "schedule_due_events",
    "schedule_source_refreshes",
    "schedule_retention",
)


class _Stop(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", STEPS)
async def test_a_failing_step_does_not_stop_the_other_steps_of_the_cycle(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    ran: list[str] = []

    def step(name: str):  # type: ignore[no-untyped-def]
        async def run() -> None:
            ran.append(name)
            if name == failing:
                raise RuntimeError("boom")

        return run

    for name in STEPS:
        monkeypatch.setattr(scheduler, name, step(name))

    async def beat(*_: object) -> None:
        ran.append("heartbeat")

    async def stop(_: float) -> None:
        raise _Stop

    monkeypatch.setattr(scheduler.heartbeat, "beat", beat)
    monkeypatch.setattr(asyncio, "sleep", stop)

    with pytest.raises(_Stop):
        await scheduler.run_scheduler()

    assert ran == [*STEPS, "heartbeat"]
