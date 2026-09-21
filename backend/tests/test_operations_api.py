import asyncio
import time
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.auth.routes import current_session
from app.main import create_app
from app.operations import heartbeat, probes
from app.operations import schemas as ops_schemas
from app.operations.routes import router

PREFIX = "/api/v1/operations"
PATHS = {f"{PREFIX}/{name}" for name in ("health", "pipelines", "feeds", "storage", "failures")}


def _operations() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item for path, item in paths.items() if path.startswith(PREFIX)}


def _query(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "query"}


def test_the_five_operations_routes_are_documented_and_read_only() -> None:
    found = _operations()
    assert set(found) == PATHS
    for item in found.values():
        assert set(item) == {"get"}
        headers = [p["name"] for p in item["get"].get("parameters", []) if p["in"] == "header"]
        assert "X-CSRF-Token" not in headers


def test_every_operations_route_requires_a_session() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 5
    for route in routes:
        assert current_session in [d.call for d in route.dependant.dependencies], route.path


def test_windows_and_areas_are_bounded() -> None:
    found = _operations()
    for name in ("pipelines", "feeds", "failures"):
        hours = _query(found[f"{PREFIX}/{name}"]["get"])["hours"]["schema"]
        assert (hours["minimum"], hours["maximum"]) == (1, 168), name
    failures = _query(found[f"{PREFIX}/failures"]["get"])
    assert failures["area"]["required"] is True
    assert set(failures["area"]["schema"]["enum"]) == {
        "feed", "article", "search", "nlp", "cluster", "monitor", "event"
    }  # fmt: skip
    assert failures["limit"]["schema"]["maximum"] == 100


def test_nothing_is_scored_ranked_or_named_after_a_user() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    ours = [n for n in schemas if getattr(ops_schemas, n, None) is not None]
    assert {"HealthResponse", "PipelinesResponse", "FeedsResponse", "MonitorPipeline"} <= set(ours)
    words = set(str({n: schemas[n] for n in ours}).lower().replace("'", " ").split())
    assert not {"score", "rank", "winner", "health_percent", "grade"} & words
    # Monitors belong to users: the global summary carries counts, never a name, query or owner.
    monitor = set(schemas["MonitorPipeline"]["properties"])
    assert not {"name", "state", "user_id", "owner", "query"} & monitor


@pytest.mark.asyncio
async def test_a_probe_that_hangs_is_down_within_its_timeout() -> None:
    async def hang() -> tuple[probes.State, str | None]:
        await asyncio.sleep(30)
        return "ok", None

    started = time.monotonic()
    result = await probes.probe("redis", hang, seconds=0.2)
    assert time.monotonic() - started < 2
    assert (result.name, result.state) == ("redis", "down")
    assert result.latency_ms is None and "timed out" in (result.detail or "")


@pytest.mark.asyncio
async def test_a_failing_probe_names_the_error_class_and_nothing_else() -> None:
    async def fail() -> tuple[probes.State, str | None]:
        raise ConnectionError("redis://user:secret@redis:6379/0 refused")

    result = await probes.probe("redis", fail)
    assert result.state == "down"
    assert result.detail == "ConnectionError"
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_one_failing_probe_does_not_stop_the_others() -> None:
    async def fine() -> tuple[probes.State, str | None]:
        return "degraded", "yellow"

    async def fail() -> tuple[probes.State, str | None]:
        raise RuntimeError

    found = await probes.run_all([("a", fail), ("b", fine)], seconds=1)
    assert [(p.name, p.state) for p in found] == [("a", "down"), ("b", "degraded")]
    assert found[1].latency_ms is not None and found[1].detail == "yellow"


@pytest.mark.parametrize(
    ("age", "state"),
    [(None, "unknown"), (0.0, "ok"), (59.0, "ok"), (61.0, "down"), (86_000.0, "down")],
)
def test_scheduler_state_follows_the_heartbeat_age(age: float | None, state: str) -> None:
    assert heartbeat.scheduler_state(age)[0] == state
    assert heartbeat.scheduler_state(age)[1]


def test_the_queue_layout_lists_every_queue_the_actors_use() -> None:
    import app.jobs.articles
    import app.jobs.clustering
    import app.jobs.diagnostics
    import app.jobs.events
    import app.jobs.ingestion
    import app.jobs.monitors
    import app.jobs.nlp
    import app.jobs.search  # noqa: F401
    from app.jobs.broker import broker

    assert set(probes.QUEUES) == set(broker.get_declared_queues())


@pytest.mark.parametrize(
    ("ages", "state"),
    [
        ({"nlp": None, "search": None}, "unknown"),
        ({"nlp": 3.0, "search": 59.0}, "ok"),
        ({"nlp": 312.0, "search": 3.0}, "down"),
        ({"nlp": None, "search": 3.0}, "down"),
    ],
)
def test_workers_state_needs_a_live_worker_on_every_queue(
    ages: dict[str, float | None], state: str
) -> None:
    found, detail = heartbeat.workers_state(ages)
    assert found == state
    if state == "down":
        assert "nlp" in detail and "search" not in detail


def test_a_booted_worker_beats_for_the_queues_it_consumes() -> None:
    from app.jobs.broker import QueueHeartbeat

    class Client:
        def __init__(self) -> None:
            self.keys: dict[str, str] = {}

        def set(self, key: str, value: str, ex: int) -> None:
            self.keys[key] = value

    class Broker:
        client = Client()

        def get_declared_queues(self) -> set[str]:
            return {"nlp", "events"}

    class Worker:
        consumer_whitelist = None

    broker = Broker()
    QueueHeartbeat().after_worker_boot(broker, Worker())
    deadline = time.monotonic() + 2
    while len(broker.client.keys) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert set(broker.client.keys) == {
        "newsintel:heartbeat:queue:nlp",
        "newsintel:heartbeat:queue:events",
    }
