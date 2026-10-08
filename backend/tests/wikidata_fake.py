"""A fake api.php that answers from what a test put in it, and records every request.

It speaks the three calls the client makes (wbsearchentities, wbgetentities, prop=info) and
wbgetclaims, in formatversion=2 JSON. Tests pair it with a MemoryThrottle on a fake clock, so the
waits between requests cost no time.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.wikidata.client import WikidataClient
from app.wikidata.throttle import MemoryThrottle


class Clock:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _snak(prop: str, qid: str) -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"value": {"entity-type": "item", "id": qid}, "type": "wikibase-entityid"},
        },
        "type": "statement",
        "rank": "normal",
    }


def entity(
    qid: str,
    *,
    labels: dict[str, str] | None = None,
    aliases: dict[str, list[str]] | None = None,
    descriptions: dict[str, str] | None = None,
    instance_of: tuple[str, ...] = (),
    subclass_of: tuple[str, ...] = (),
    sitelinks: int = 0,
    revision: int = 100,
) -> dict[str, Any]:
    """An item as wbgetentities returns it, with every field the client may ask for."""
    claims: dict[str, list[dict[str, Any]]] = {}
    if instance_of:
        claims["P31"] = [_snak("P31", value) for value in instance_of]
    if subclass_of:
        claims["P279"] = [_snak("P279", value) for value in subclass_of]
    return {
        "type": "item",
        "id": qid,
        "lastrevid": revision,
        "labels": {key: {"language": key, "value": value} for key, value in (labels or {}).items()},
        "aliases": {
            key: [{"language": key, "value": value} for value in values]
            for key, values in (aliases or {}).items()
        },
        "descriptions": {
            key: {"language": key, "value": value} for key, value in (descriptions or {}).items()
        },
        "claims": claims,
        "sitelinks": {f"wiki{index}": {"site": f"wiki{index}"} for index in range(sitelinks)},
    }


class FakeWikidata:
    """Items and search answers a test sets up; `requests` lists (action, detail) in order."""

    def __init__(self) -> None:
        self.entities: dict[str, dict[str, Any]] = {}
        self.searches: dict[tuple[str, str], list[str]] = {}
        # Items Wikidata merged into another: source QID -> target QID.
        self.redirects: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        # Set to a response to answer every request with it (a 429, a 503, a dropped line).
        self.failure: Callable[[httpx.Request], httpx.Response] | None = None

    def add(self, *items: dict[str, Any]) -> None:
        for item in items:
            self.entities[item["id"]] = item

    def actions(self, action: str) -> list[str]:
        return [detail for kind, detail in self.requests if kind == action]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        action = params.get("action", "")
        if action == "query":
            detail = params.get("titles", "")
        elif action == "wbsearchentities":
            detail = f"{params.get('language')}:{params.get('search')}"
        elif action == "wbgetentities":
            detail = params.get("ids", "")
        else:
            detail = f"{params.get('entity')}:{params.get('property')}"
        self.requests.append((action, detail))
        if self.failure is not None:
            return self.failure(request)
        if action == "wbsearchentities":
            found = self.searches.get((params["language"], params["search"]), [])
            return httpx.Response(
                200, json={"search": [{"id": qid} for qid in found], "success": 1}
            )
        if action == "wbgetentities":
            props = params.get("props", "").split("|")
            entities: dict[str, Any] = {}
            for asked in params["ids"].split("|"):
                qid = self.redirects.get(asked, asked)
                stored = self.entities.get(qid)
                if stored is None:
                    entities[asked] = {"id": asked, "missing": ""}
                    continue
                answer = {key: value for key, value in stored.items() if key != "claims"}
                if "claims" in props:
                    answer["claims"] = stored["claims"]
                if qid != asked:
                    answer["redirects"] = {"from": asked, "to": qid}
                entities[qid] = answer
            return httpx.Response(200, json={"entities": entities, "success": 1})
        if action == "query":
            pages = []
            redirects = []
            for asked in params["titles"].split("|"):
                qid = self.redirects.get(asked, asked)
                if qid != asked:
                    redirects.append({"from": asked, "to": qid})
                stored = self.entities.get(qid)
                if stored is None:
                    pages.append({"ns": 0, "title": qid, "missing": True})
                else:
                    pages.append(
                        {"ns": 0, "title": qid, "lastrevid": stored["lastrevid"], "length": 1000}
                    )
            query: dict[str, Any] = {"pages": pages}
            if redirects:
                query["redirects"] = redirects
            return httpx.Response(200, json={"batchcomplete": True, "query": query})
        if action == "wbgetclaims":
            stored = self.entities.get(params["entity"], {})
            prop = params["property"]
            claims = {prop: stored.get("claims", {}).get(prop, [])}
            return httpx.Response(200, json={"claims": claims})
        return httpx.Response(400, json={"error": {"code": "badaction", "info": action}})


def settings(**values: Any) -> Settings:
    return Settings(wikidata_contact="newsintel-owner@example.org", **values)


def client(fake: FakeWikidata, config: Settings, clock: Clock | None = None) -> WikidataClient:
    clock = clock or Clock()
    return WikidataClient(
        config,
        MemoryThrottle(config, clock=clock, sleep=clock.sleep),
        transport=httpx.MockTransport(fake),
    )
