"""Push notifications through ntfy: quiet hours, the daily summary and the messages themselves."""

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.investigations.schemas import InvestigationState
from app.monitors import evaluation
from app.monitors.evaluation import Snapshot, search_evaluator
from app.notifications.ntfy import Message, NtfyError, publish
from app.notifications.schemas import NotificationSettingsUpdate
from app.notifications.service import (
    combined_message,
    digest_due,
    digest_message,
    in_quiet_hours,
    monitor_message,
)

ATHENS = "Europe/Athens"


def _at(hour: int, minute: int = 0, day: int = 12) -> datetime:
    # 2026-10-12 is in summer time in Athens, UTC+3.
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC) - timedelta(hours=3)


@pytest.mark.parametrize(
    ("local", "quiet"),
    [((22, 59), False), ((23, 0), True), ((2, 30), True), ((6, 59), True), ((7, 0), False)],
)
def test_quiet_hours_wrap_past_midnight_in_the_chosen_zone(
    local: tuple[int, int], quiet: bool
) -> None:
    assert in_quiet_hours(_at(*local), time(23), time(7), ATHENS) is quiet


def test_quiet_hours_within_a_day_and_when_unset() -> None:
    assert in_quiet_hours(_at(13), time(12), time(14), ATHENS)
    assert not in_quiet_hours(_at(14), time(12), time(14), ATHENS)
    assert not in_quiet_hours(_at(3), None, None, ATHENS)


def test_the_daily_summary_is_due_once_a_day_from_its_hour() -> None:
    assert not digest_due(_at(7, 59), 8, ATHENS, None)
    assert digest_due(_at(8), 8, ATHENS, None)
    assert digest_due(_at(21), 8, ATHENS, date(2026, 10, 11))
    assert not digest_due(_at(21), 8, ATHENS, date(2026, 10, 12))
    # Just after midnight in Athens is still the previous evening in UTC: the local day counts.
    assert not digest_due(_at(0, 30, day=13), 8, ATHENS, date(2026, 10, 12))


def test_messages_name_the_investigation_count_and_link_to_it() -> None:
    one = monitor_message("Energy grid", 3, "https://news.example/monitors/?id=m1")
    assert one.title == "3 new articles: Energy grid"
    assert one.click == "https://news.example/monitors/?id=m1"
    assert monitor_message("Ενέργεια", 1, None).title == "1 new article: Ενέργεια"

    many = combined_message(
        [("Energy grid", 3), ("Harbor", 1), ("Acme", 2), ("Ports", 5)],
        "https://news.example/investigations/",
    )
    assert many.title == "New articles in 4 investigations"
    assert many.body.splitlines() == [
        "Energy grid: 3 new articles",
        "Harbor: 1 new article",
        "Acme: 2 new articles",
        "Ports: 5 new articles",
    ]

    summary = digest_message([("Energy grid", 5, 1), ("Harbor", 1, 2)], None)
    assert summary.title == "Daily summary: 2 investigations have news"
    assert summary.body.splitlines() == [
        "Energy grid: 5 new articles, 1 new story",
        "Harbor: 1 new article, 2 new stories",
    ]
    assert summary.click is None


@pytest.mark.asyncio
async def test_publish_posts_json_to_the_server_with_the_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "x"})

    message = Message(title="3 new articles: Ενέργεια", body="Body", click="https://n.example/")
    await publish(
        "https://ntfy.example/",
        "news-intel",
        "tk_secret",
        message,
        transport=httpx.MockTransport(handler),
    )

    (request,) = requests
    assert str(request.url) == "https://ntfy.example/"
    assert request.headers["Authorization"] == "Bearer tk_secret"
    sent: dict[str, Any] = json.loads(request.content)
    assert sent == {
        "topic": "news-intel",
        "title": "3 new articles: Ενέργεια",
        "message": "Body",
        "click": "https://n.example/",
        "tags": ["newspaper"],
    }


@pytest.mark.asyncio
async def test_publish_reports_a_refusal_and_an_unreachable_server() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(NtfyError, match="403"):
        await publish(
            "https://ntfy.example",
            "t",
            None,
            Message("a", "b"),
            transport=httpx.MockTransport(refused),
        )
    with pytest.raises(NtfyError, match="could not reach"):
        await publish(
            "https://ntfy.example",
            "t",
            None,
            Message("a", "b"),
            transport=httpx.MockTransport(unreachable),
        )


def test_settings_are_checked_before_they_are_kept() -> None:
    good = NotificationSettingsUpdate(
        enabled=True,
        server="https://ntfy.sh",
        topic="news-intel_1",
        timezone=ATHENS,
        quiet_start=time(23),
        quiet_end=time(7),
        digest_enabled=True,
        digest_hour=8,
    )
    assert good.server == "https://ntfy.sh"
    for bad in (
        {"server": "ftp://ntfy.sh"},
        {"topic": "has space"},
        {"timezone": "Mars/Base"},
        {"quiet_end": None},
        {"digest_hour": 24},
        {"public_url": "news.example"},
        {"enabled": True, "server": None},
    ):
        with pytest.raises(ValidationError):
            NotificationSettingsUpdate(**{**good.model_dump(), **bad})


# ---- what the evaluation hands to notifications ----

HORIZON = datetime(2026, 9, 20, 12, tzinfo=UTC)
EARLIER = HORIZON - timedelta(minutes=5)


class FakeAdapter:
    def __init__(self, *totals: int) -> None:
        self.totals = list(totals)
        self.bodies: list[dict[str, Any]] = []

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        total = self.totals.pop(0)
        hits = (
            [
                {
                    "_source": {
                        "article_id": "00000000-0000-0000-0000-000000000001",
                        "first_discovered_at": EARLIER.isoformat(),
                    }
                }
            ]
            if total
            else []
        )
        return {
            "hits": {"total": {"value": total}, "hits": hits},
            "aggregations": {"clusters": {"value": 1}},
        }


@pytest.fixture
def _stub_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def criteria(db: object, **params: object) -> str:
        return "criteria"

    async def target(db: object) -> str:
        return "index"

    monkeypatch.setattr(evaluation, "search_criteria", criteria)
    monkeypatch.setattr(evaluation, "current_search_target", target)
    monkeypatch.setattr(evaluation, "build_query", lambda *_: {"match_all": {}})


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_search")
async def test_the_window_counts_articles_new_since_the_last_evaluation() -> None:
    snapshot = Snapshot("search", InvestigationState(q="grid"), EARLIER, EARLIER, EARLIER)

    window = await search_evaluator(None, FakeAdapter(3, 7), snapshot, HORIZON)  # type: ignore[arg-type]

    assert (window.new_articles, window.unseen_articles) == (3, 7)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_search")
async def test_a_rewound_window_counts_only_what_was_never_announced() -> None:
    # A view rewound the evaluation cursor to an hour ago; notifications went out up to EARLIER.
    rewound = EARLIER - timedelta(hours=1)
    snapshot = Snapshot("search", InvestigationState(q="grid"), rewound, rewound, EARLIER)

    adapter = FakeAdapter(9, 9, 2)
    window = await search_evaluator(None, adapter, snapshot, HORIZON)  # type: ignore[arg-type]

    assert (window.new_articles, window.unseen_articles) == (2, 9)
    fresh = adapter.bodies[2]["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]
    assert fresh == {"gt": EARLIER.isoformat(), "lte": HORIZON.isoformat()}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_stub_search")
async def test_the_first_evaluation_announces_nothing() -> None:
    snapshot = Snapshot("search", InvestigationState(q="grid"), None, None, None)

    window = await search_evaluator(None, FakeAdapter(40), snapshot, HORIZON)  # type: ignore[arg-type]

    assert window.new_articles == 0
