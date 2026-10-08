"""The contract check that asks the real API (from a GitHub workflow) reads our fixtures too."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.wikidata.client import WikidataClient
from app.wikidata.contract import CLASS_ROOTS, check
from app.wikidata.throttle import MemoryThrottle

FIXTURES = Path(__file__).parent / "fixtures" / "wikidata"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _api(adams: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        action = params["action"]
        if action == "wbsearchentities":
            return httpx.Response(200, json=_fixture("search.json"))
        if action == "query":
            pages = [
                {"ns": 0, "title": qid, "lastrevid": 5, "length": 1000}
                for qid in params["titles"].split("|")
            ]
            return httpx.Response(200, json={"query": {"pages": pages}})
        if action == "wbgetclaims":
            return httpx.Response(200, json={"claims": {"P31": adams["claims"]["P31"]}})
        ids = params["ids"].split("|")
        entities = {
            qid: adams
            if qid == "Q42"
            else {
                "id": qid,
                "lastrevid": 3,
                "labels": {"en": {"language": "en", "value": f"class {qid}"}},
            }
            for qid in ids
        }
        return httpx.Response(200, json={"entities": entities, "success": 1})

    return httpx.MockTransport(handler)


async def _run(adams: dict[str, Any]) -> list[str]:
    settings = Settings(wikidata_contact="owner@example.org", wikidata_min_interval_seconds=1)

    async def no_wait(_seconds: float) -> None:
        return None

    throttle = MemoryThrottle(settings, sleep=no_wait)
    async with WikidataClient(settings, throttle, transport=_api(adams)) as client:
        report = await check(client)
    return report.problems


@pytest.mark.asyncio
async def test_the_contract_passes_on_answers_shaped_like_the_fixtures() -> None:
    adams = _fixture("entities.json")["entities"]["Q42"]
    assert await _run(adams) == []


@pytest.mark.asyncio
async def test_the_contract_names_what_changed_in_the_answers() -> None:
    adams = _fixture("entities.json")["entities"]["Q42"]
    del adams["claims"]["P214"]
    adams["lastrevid"] = "not a number"
    problems = await _run(adams)
    assert any("viaf" in problem for problem in problems)
    assert any("revision" in problem for problem in problems)


def test_the_type_map_roots_are_the_ones_the_contract_checks() -> None:
    from app.wikidata.types import TYPE_ROOTS

    assert {qid for roots in TYPE_ROOTS.values() for qid in roots} == set(CLASS_ROOTS)
