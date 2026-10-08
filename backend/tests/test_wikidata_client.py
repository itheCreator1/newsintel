"""The Wikidata client keeps far inside Wikimedia's limits, and stops rather than retries.

Every test runs on a fake clock: `sleep` moves it forward, so the waits are checked exactly and
cost no time. No test reaches the network.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.wikidata.client import WikidataClient, contact_ok, user_agent
from app.wikidata.errors import (
    WikidataBudgetSpent,
    WikidataDisabled,
    WikidataPaused,
    WikidataUnavailable,
)
from app.wikidata.throttle import MemoryThrottle

FIXTURES = Path(__file__).parent / "fixtures" / "wikidata"
START = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
APP = Path(__file__).resolve().parents[1] / "app"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class Clock:
    def __init__(self) -> None:
        self.now = START
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def _settings(**values: Any) -> Settings:
    return Settings(wikidata_contact="newsintel-owner@example.org", **values)


class Server:
    """A scripted api.php: each request takes the next response; `took` is its server time."""

    def __init__(self, clock: Clock, *responses: httpx.Response, took: float = 0.2) -> None:
        self.clock = clock
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.started: list[datetime] = []
        self.took = took

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.started.append(self.clock.now)
        self.clock.now += timedelta(seconds=self.took)
        if not self.responses:
            return httpx.Response(200, json=_fixture("search-empty.json"))
        return self.responses.pop(0)


def _client(
    settings: Settings, clock: Clock, server: Callable[[httpx.Request], httpx.Response]
) -> WikidataClient:
    return WikidataClient(
        settings,
        MemoryThrottle(settings, clock=clock, sleep=clock.sleep),
        transport=httpx.MockTransport(server),
    )


def _ok(name: str = "search-empty.json") -> httpx.Response:
    return httpx.Response(200, json=_fixture(name))


# Identity --------------------------------------------------------------------------------


def test_the_user_agent_names_the_app_and_a_way_to_reach_its_owner() -> None:
    agent = user_agent("owner@example.org")
    # Wikimedia's format: name/version (contact) library/version.
    assert re.fullmatch(r"NewsIntel/[\w.]+ \(owner@example\.org\) python-httpx/[\w.]+", agent)


@pytest.mark.parametrize(
    ("contact", "ok"),
    [
        ("owner@example.org", True),
        ("https://example.org/newsintel", True),
        ("", False),
        ("   ", False),
        ("owner", False),
        ("http://", False),
        ("a@b", False),
        ("owner@example.org (x)", False),
    ],
)
def test_only_a_real_email_or_url_counts_as_a_contact(contact: str, ok: bool) -> None:
    assert contact_ok(contact) is ok


@pytest.mark.asyncio
async def test_every_request_identifies_itself_asks_for_gzip_and_sets_maxlag() -> None:
    clock = Clock()
    server = Server(clock, _ok("search.json"))
    async with _client(_settings(), clock, server) as client:
        assert await client.search("Douglas Adams", "en") == ["Q42", "Q9000002"]
    request = server.requests[0]
    assert request.headers["user-agent"] == user_agent("newsintel-owner@example.org")
    assert "gzip" in request.headers["accept-encoding"]
    assert request.url.host == "www.wikidata.org"
    assert request.method == "GET"
    params = dict(request.url.params)
    assert params["maxlag"] == "5"
    assert params["format"] == "json"
    assert params["formatversion"] == "2"
    assert params["action"] == "wbsearchentities"
    assert params["search"] == "Douglas Adams"
    assert params["language"] == "en"
    assert params["type"] == "item"
    assert int(params["limit"]) <= 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"wikidata_contact": ""},
        {"wikidata_contact": "not a contact"},
        {"wikidata_contact": "owner@example.org", "wikidata_enabled": False},
    ],
)
async def test_without_a_contact_or_when_switched_off_nothing_is_sent(
    values: dict[str, Any],
) -> None:
    clock = Clock()
    server = Server(clock)
    settings = Settings(**values)
    async with _client(settings, clock, server) as client:
        assert client.enabled is False
        with pytest.raises(WikidataDisabled):
            await client.search("Douglas Adams", "en")
    assert server.requests == []


# Pace ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_go_one_at_a_time_at_least_three_seconds_apart() -> None:
    clock = Clock()
    server = Server(clock)
    async with _client(_settings(), clock, server) as client:
        for name in ("a", "b", "c", "d"):
            await client.search(name, "en")
    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(server.started, server.started[1:], strict=False)
    ]
    assert len(gaps) == 3
    # Three seconds from the end of one request to the start of the next: at most 20 a minute.
    assert all(gap >= 3.0 + server.took for gap in gaps)


@pytest.mark.asyncio
async def test_a_slow_answer_makes_the_next_request_wait_five_seconds() -> None:
    clock = Clock()
    server = Server(clock, took=1.5)
    async with _client(_settings(), clock, server) as client:
        await client.search("a", "en")
        await client.search("b", "en")
    assert (server.started[1] - server.started[0]).total_seconds() >= 1.5 + 5.0


@pytest.mark.asyncio
async def test_the_pace_is_a_setting_but_never_faster_than_one_a_second() -> None:
    with pytest.raises(ValueError):
        Settings(wikidata_min_interval_seconds=0.5)
    clock = Clock()
    server = Server(clock)
    async with _client(_settings(wikidata_min_interval_seconds=10), clock, server) as client:
        await client.search("a", "en")
        await client.search("b", "en")
    assert (server.started[1] - server.started[0]).total_seconds() >= 10


# Stopping --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_429_stops_all_requests_for_at_least_fifteen_minutes() -> None:
    clock = Clock()
    server = Server(clock, httpx.Response(429, headers={"retry-after": "30"}))
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable) as raised:
            await client.search("a", "en")
        assert raised.value.outcome == "rate_limited"
        clock.now += timedelta(minutes=14)
        with pytest.raises(WikidataPaused) as paused:
            await client.search("b", "en")
        assert paused.value.reason == "rate_limited"
        assert len(server.requests) == 1
        clock.now += timedelta(minutes=2)
        await client.search("c", "en")
    assert len(server.requests) == 2


@pytest.mark.asyncio
async def test_a_longer_retry_after_is_honoured() -> None:
    clock = Clock()
    server = Server(clock, httpx.Response(429, headers={"retry-after": "7200"}))
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")
        clock.now += timedelta(minutes=119)
        with pytest.raises(WikidataPaused):
            await client.search("b", "en")


@pytest.mark.asyncio
async def test_a_retry_after_given_as_a_date_is_honoured() -> None:
    clock = Clock()
    until = START + timedelta(hours=3)
    header = until.strftime("%a, %d %b %Y %H:%M:%S GMT")
    server = Server(clock, httpx.Response(429, headers={"retry-after": header}))
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")
        clock.now = until - timedelta(minutes=1)
        with pytest.raises(WikidataPaused):
            await client.search("b", "en")


@pytest.mark.asyncio
async def test_a_second_429_within_the_hour_stops_requests_for_a_day() -> None:
    clock = Clock()
    server = Server(
        clock,
        httpx.Response(429),
        httpx.Response(429),
    )
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")
        clock.now += timedelta(minutes=16)
        with pytest.raises(WikidataUnavailable):
            await client.search("b", "en")
        clock.now += timedelta(hours=23)
        with pytest.raises(WikidataPaused):
            await client.search("c", "en")
    assert len(server.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(503, headers={"retry-after": "60"}),
    ],
)
async def test_a_429_or_503_without_a_long_retry_after_still_pauses_fifteen_minutes(
    response: httpx.Response,
) -> None:
    clock = Clock()
    server = Server(clock, response)
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable) as raised:
            await client.search("a", "en")
        assert raised.value.outcome == "rate_limited"
        began = clock.now
        with pytest.raises(WikidataPaused) as paused:
            await client.search("b", "en")
    assert paused.value.until - began >= timedelta(minutes=15) - timedelta(seconds=1)
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_maxlag_is_a_pause_not_a_result_and_repeated_errors_pause_longer() -> None:
    clock = Clock()
    # The lag error comes back with HTTP 200: only the body tells.
    lagged = httpx.Response(200, json=_fixture("maxlag.json"), headers={"retry-after": "5"})
    server = Server(
        clock,
        lagged,
        httpx.Response(502),
        *(httpx.Response(500) for _ in range(6)),
        _ok(),
    )
    pauses: list[float] = []
    async with _client(_settings(), clock, server) as client:
        for _ in range(8):
            with pytest.raises(WikidataUnavailable) as raised:
                await client.search("a", "en")
            assert raised.value.outcome == "error"
            began = clock.now
            with pytest.raises(WikidataPaused) as paused:
                await client.search("a", "en")
            pauses.append((paused.value.until - began).total_seconds() / 60)
            clock.now = paused.value.until
        await client.search("a", "en")
        # A good answer starts the count again.
        server.responses.append(httpx.Response(500))
        clock.now += timedelta(seconds=10)
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")
        began = clock.now
        with pytest.raises(WikidataPaused) as paused:
            await client.search("a", "en")
    assert [round(minutes) for minutes in pauses] == [5, 10, 20, 40, 80, 160, 320, 360]
    assert round((paused.value.until - began).total_seconds() / 60) == 5


@pytest.mark.asyncio
async def test_a_longer_retry_after_on_an_error_is_honoured() -> None:
    clock = Clock()
    server = Server(clock, httpx.Response(500, headers={"retry-after": "3600"}))
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")
        clock.now += timedelta(minutes=59)
        with pytest.raises(WikidataPaused):
            await client.search("b", "en")


@pytest.mark.asyncio
async def test_a_network_failure_or_timeout_pauses_instead_of_retrying() -> None:
    clock = Clock()

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with _client(_settings(), clock, unreachable) as client:
        with pytest.raises(WikidataUnavailable) as raised:
            await client.search("a", "en")
        assert raised.value.outcome == "error"
        with pytest.raises(WikidataPaused):
            await client.search("a", "en")


@pytest.mark.asyncio
async def test_a_403_means_we_may_be_blocked_and_stops_requests_for_a_day() -> None:
    clock = Clock()
    server = Server(clock, httpx.Response(403, text="Please set a user-agent"))
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(WikidataUnavailable) as raised:
            await client.search("a", "en")
        assert raised.value.outcome == "blocked"
        clock.now += timedelta(hours=23, minutes=59)
        with pytest.raises(WikidataPaused) as paused:
            await client.search("a", "en")
        assert paused.value.reason == "blocked"


@pytest.mark.asyncio
async def test_an_api_error_or_an_unreadable_answer_pauses_too() -> None:
    for response in (
        httpx.Response(200, json=_fixture("error-badparam.json")),
        httpx.Response(200, text="<html>not json</html>"),
    ):
        clock = Clock()
        server = Server(clock, response)
        async with _client(_settings(), clock, server) as client:
            with pytest.raises(WikidataUnavailable):
                await client.search("a", "en")
            with pytest.raises(WikidataPaused):
                await client.search("a", "en")


@pytest.mark.asyncio
async def test_an_oversized_answer_is_cut_off_and_counts_as_an_error() -> None:
    clock = Clock()
    server = Server(clock, httpx.Response(200, content=b'{"search": [' + b" " * 5000 + b"]}"))
    async with _client(_settings(wikidata_max_response_bytes=1000), clock, server) as client:
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")


@pytest.mark.asyncio
async def test_the_daily_budget_stops_requests_until_tomorrow() -> None:
    clock = Clock()
    server = Server(clock)
    async with _client(_settings(wikidata_daily_request_budget=2), clock, server) as client:
        await client.search("a", "en")
        await client.search("b", "en")
        with pytest.raises(WikidataBudgetSpent):
            await client.search("c", "en")
        clock.now = datetime(2026, 10, 9, 0, 0, 1, tzinfo=UTC)
        await client.search("d", "en")
    assert len(server.requests) == 3


@pytest.mark.asyncio
async def test_the_counts_are_kept_per_kind_and_outcome() -> None:
    clock = Clock()
    server = Server(clock, _ok(), httpx.Response(429))
    settings = _settings()
    throttle = MemoryThrottle(settings, clock=clock, sleep=clock.sleep)
    async with WikidataClient(settings, throttle, transport=httpx.MockTransport(server)) as client:
        await client.search("a", "en")
        with pytest.raises(WikidataUnavailable):
            await client.search("b", "en")
    assert throttle.counts == {
        ("2026-10-08", "search", "ok"): 1,
        ("2026-10-08", "search", "rate_limited"): 1,
    }


# Batches ---------------------------------------------------------------------------------


def _titles(request: httpx.Request) -> list[str]:
    params = request.url.params
    return (params.get("titles") or params.get("ids") or "").split("|")


@pytest.mark.asyncio
async def test_revision_checks_ask_for_fifty_items_at_a_time() -> None:
    clock = Clock()
    qids = [f"Q{n}" for n in range(1, 121)]

    def server(request: httpx.Request) -> httpx.Response:
        clock.now += timedelta(seconds=0.1)
        pages = [{"ns": 0, "title": qid, "lastrevid": 1, "length": 100} for qid in _titles(request)]
        return httpx.Response(200, json={"query": {"pages": pages}})

    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return server(request)

    async with _client(_settings(), clock, recording) as client:
        pages = await client.info(qids)
    assert sorted(pages) == sorted(qids)
    assert [len(_titles(request)) for request in seen] == [50, 50, 20]
    assert {request.url.params["action"] for request in seen} == {"query"}
    assert {request.url.params["prop"] for request in seen} == {"info"}


def _entities_answer(request: httpx.Request) -> httpx.Response:
    entities = {
        qid: {
            "id": qid,
            "lastrevid": 7,
            "labels": {"en": {"language": "en", "value": f"Item {qid}"}},
        }
        for qid in _titles(request)
    }
    return httpx.Response(200, json={"entities": entities, "success": 1})


@pytest.mark.asyncio
async def test_candidates_are_fetched_light_fifty_at_a_time_without_claims() -> None:
    clock = Clock()
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        clock.now += timedelta(seconds=0.1)
        return _entities_answer(request)

    qids = [f"Q{n}" for n in range(1, 61)]
    async with _client(_settings(), clock, server) as client:
        items = await client.items([*qids, "Q3"])
    assert sorted(item.qid for item in items) == sorted(qids)
    assert [len(_titles(request)) for request in seen] == [50, 10]
    params = seen[0].url.params
    assert params["action"] == "wbgetentities"
    # The claims of a big item (a country) run to megabytes: never for a mere candidate.
    assert params["props"] == "labels|aliases|descriptions|sitelinks|info"
    assert params["languages"] == "el|en"


@pytest.mark.asyncio
async def test_a_full_fetch_checks_sizes_first_and_keeps_each_batch_small() -> None:
    clock = Clock()
    seen: list[httpx.Request] = []
    sizes = {"Q1": 1_500_000, "Q2": 1_500_000, "Q3": 1000, "Q4": 1000, "Q5": 1000}

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        clock.now += timedelta(seconds=0.1)
        if request.url.params["action"] == "query":
            pages = [
                {"ns": 0, "title": qid, "lastrevid": 7, "length": sizes[qid]}
                for qid in _titles(request)
            ]
            return httpx.Response(200, json={"query": {"pages": pages}})
        return _entities_answer(request)

    async with _client(_settings(), clock, server) as client:
        items = await client.items(["Q1", "Q2", "Q3", "Q4", "Q5", "Q3"], full=True)
    assert sorted(item.qid for item in items) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert seen[0].url.params["action"] == "query"
    fetches = [request for request in seen if request.url.params["action"] == "wbgetentities"]
    # Two 1.5 MB items never share a request under the 2 MB budget; the small ones ride along.
    assert len(fetches) == 2
    for request in fetches:
        titles = _titles(request)
        assert len(titles) == 1 or sum(sizes[qid] for qid in titles) <= 2_000_000
    assert fetches[0].url.params["props"] == "labels|aliases|descriptions|claims|sitelinks|info"


@pytest.mark.asyncio
async def test_a_type_check_reads_one_property_of_one_item() -> None:
    clock = Clock()
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        claims = _fixture("entities.json")["entities"]["Q9000002"]["claims"]
        return httpx.Response(200, json={"claims": claims})

    async with _client(_settings(), clock, server) as client:
        assert await client.claim_items("Q9000002", "P31") == ["Q43229"]
    params = seen[0].url.params
    assert params["action"] == "wbgetclaims"
    assert params["entity"] == "Q9000002"
    assert params["property"] == "P31"
    assert params["props"] == ""


@pytest.mark.asyncio
async def test_missing_and_redirected_items_are_reported_without_fetching_them() -> None:
    clock = Clock()
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        clock.now += timedelta(seconds=0.1)
        if request.url.params["action"] == "query":
            return httpx.Response(200, json=_fixture("info.json"))
        return httpx.Response(
            200,
            json={
                "entities": {
                    "Q42": _fixture("entities.json")["entities"]["Q42"],
                    "Q9000004": {"id": "Q9000004", "lastrevid": 1800, "labels": {}},
                },
                "success": 1,
            },
        )

    async with _client(_settings(), clock, server) as client:
        items = {
            item.qid: item
            for item in await client.items(["Q42", "Q9000003", "Q9000005"], full=True)
        }
    assert items["Q9000003"].state == "redirected"
    assert items["Q9000003"].redirect_to == "Q9000004"
    assert items["Q9000005"].state == "missing"
    assert items["Q9000004"].state == "ok"
    fetched = [
        _titles(request) for request in seen if request.url.params["action"] == "wbgetentities"
    ]
    assert fetched == [["Q42", "Q9000004"]]


@pytest.mark.asyncio
async def test_a_bad_qid_never_reaches_the_network() -> None:
    clock = Clock()
    server = Server(clock)
    async with _client(_settings(), clock, server) as client:
        with pytest.raises(ValueError):
            await client.items(["Q42|Q43"])
    assert server.requests == []


def test_no_other_code_talks_to_wikidata() -> None:
    """One client, one throttle: nothing else in the app may reach Wikidata."""
    outside = sorted(
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "wikidata.org" in path.read_text()
        and path.relative_to(APP).as_posix() not in {"core/config.py", "wikidata/contract.py"}
    )
    assert outside == []
    clients = sorted(
        path.relative_to(APP).as_posix()
        for path in (APP / "wikidata").rglob("*.py")
        if "httpx.AsyncClient" in path.read_text()
    )
    assert clients == ["wikidata/client.py"]
