import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest

from app.investigations.schemas import InvestigationState
from app.monitors import evaluation
from app.monitors.evaluation import (
    EVALUATORS,
    Snapshot,
    criteria_params,
    search_evaluator,
    window_body,
)
from app.monitors.schemas import MonitorKind

HORIZON = datetime(2026, 9, 20, 12, tzinfo=UTC)
EARLIER = HORIZON - timedelta(minutes=5)
ARTICLE = uuid.uuid4()


class FakeAdapter:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        return self.responses.pop(0)


def _response(total: int, clusters: int | None = None, at: datetime = EARLIER) -> dict[str, Any]:
    hits = (
        [{"_source": {"article_id": str(ARTICLE), "first_discovered_at": at.isoformat()}}]
        if total
        else []
    )
    body: dict[str, Any] = {"hits": {"total": {"value": total}, "hits": hits}}
    if clusters is not None:
        body["aggregations"] = {"clusters": {"value": clusters}}
    return body


@pytest.fixture(autouse=True)
def _stub_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def criteria(db: object, **params: object) -> str:
        return "criteria"

    async def target(db: object, value: object) -> tuple[str, int]:
        return "index", 3

    monkeypatch.setattr(evaluation, "search_criteria", criteria)
    monkeypatch.setattr(evaluation, "current_search_target", target)
    monkeypatch.setattr(evaluation, "build_query", lambda *_: {"match_all": {}})


def _snapshot(eval_at: datetime | None, viewed_at: datetime | None = None) -> Snapshot:
    return Snapshot("search", InvestigationState(q="grid"), eval_at, viewed_at or eval_at)


def test_criteria_params_drop_view_settings_and_keep_the_target() -> None:
    state = InvestigationState(q="grid", sort="newest", interval="week", story_country=["GR"])

    params = criteria_params(state)

    assert "sort" not in params and "interval" not in params
    assert params["q"] == "grid" and params["story_country"] == ["GR"]


def test_window_body_is_bounded_and_fetches_at_most_one_hit() -> None:
    body = window_body({"match_all": {}}, EARLIER, HORIZON, clusters=True)

    assert body["size"] == 1 and body["track_total_hits"] is True
    bounds = body["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]
    assert bounds == {"gt": EARLIER.isoformat(), "lte": HORIZON.isoformat()}
    assert body["sort"][0] == {"first_discovered_at": "desc"}
    assert body["aggs"]["clusters"]["cardinality"]["field"] == "story_cluster_id"
    open_start = window_body({"match_all": {}}, None, HORIZON, clusters=False)
    assert "gt" not in open_start["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]
    assert "aggs" not in open_start


def test_every_kind_has_an_evaluator() -> None:
    assert set(EVALUATORS) == set(get_args(MonitorKind))


@pytest.mark.asyncio
async def test_first_evaluation_baselines_both_cursors_without_counting() -> None:
    adapter = FakeAdapter(_response(40))

    window = await search_evaluator(None, adapter, _snapshot(None), HORIZON)  # type: ignore[arg-type]

    assert (window.eval_cursor_at, window.viewed_cursor_at) == (HORIZON, HORIZON)
    assert (window.unseen_articles, window.unseen_clusters) == (0, 0)
    assert window.latest_match_article_id == ARTICLE and window.latest_match_at == EARLIER
    assert len(adapter.bodies) == 1 and "gt" not in str(adapter.bodies[0]["query"])


@pytest.mark.asyncio
async def test_an_empty_window_only_advances_the_evaluation_cursor() -> None:
    adapter = FakeAdapter(_response(0))

    window = await search_evaluator(None, adapter, _snapshot(EARLIER), HORIZON)  # type: ignore[arg-type]

    assert window.eval_cursor_at == HORIZON and window.viewed_cursor_at is None
    assert window.unseen_articles is None and window.latest_match_article_id is None
    assert len(adapter.bodies) == 1 and adapter.bodies[0]["size"] == 0


@pytest.mark.asyncio
async def test_new_hits_set_counts_from_the_unseen_window() -> None:
    viewed = EARLIER - timedelta(hours=1)
    adapter = FakeAdapter(_response(3), _response(7, clusters=2))

    window = await search_evaluator(None, adapter, _snapshot(EARLIER, viewed), HORIZON)  # type: ignore[arg-type]

    assert (window.unseen_articles, window.unseen_clusters) == (7, 2)
    assert window.latest_match_article_id == ARTICLE
    delta, unseen = (body["query"]["bool"]["filter"][1]["range"] for body in adapter.bodies)
    assert delta["first_discovered_at"]["gt"] == EARLIER.isoformat()
    assert unseen["first_discovered_at"]["gt"] == viewed.isoformat()


@pytest.mark.asyncio
async def test_a_horizon_that_has_not_moved_forward_changes_nothing() -> None:
    adapter = FakeAdapter()

    window = await search_evaluator(None, adapter, _snapshot(HORIZON), HORIZON)  # type: ignore[arg-type]

    assert window.eval_cursor_at == HORIZON and window.unseen_articles is None
    assert adapter.bodies == []
