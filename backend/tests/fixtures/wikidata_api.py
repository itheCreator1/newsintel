"""A stand-in for www.wikidata.org/w/api.php, for the end-to-end tests only.

It answers the four calls the client makes (wbsearchentities, wbgetentities, prop=info and
wbgetclaims, formatversion=2) from wikidata/e2e-items.json: two invented people named
"Ada Lindqvist". The e2e stack points NEWSINTEL_WIKIDATA_URL here, so no test reaches Wikidata.
"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

DATA = json.loads((Path(__file__).parent / "wikidata" / "e2e-items.json").read_text())
PATH = "/w/api.php"


def _entity_answer(qid: str, props: list[str]) -> dict[str, Any]:
    stored = DATA["entities"].get(qid)
    if stored is None:
        return {"id": qid, "missing": ""}
    answer = {key: value for key, value in stored.items() if key != "claims"}
    if "claims" in props:
        answer["claims"] = stored["claims"]
    return answer


def answer(query: str) -> tuple[int, dict[str, Any]]:
    """The status and JSON body for one api.php query string."""
    params = {key: values[0] for key, values in parse_qs(query).items()}
    action = params.get("action", "")
    if action == "wbsearchentities":
        found = DATA["searches"].get(f"{params.get('language')}:{params.get('search')}", [])
        return 200, {"search": [{"id": qid} for qid in found], "success": 1}
    if action == "wbgetentities":
        props = params.get("props", "").split("|")
        ids = params.get("ids", "").split("|")
        return 200, {"entities": {qid: _entity_answer(qid, props) for qid in ids}, "success": 1}
    if action == "query":
        pages = []
        for qid in params.get("titles", "").split("|"):
            stored = DATA["entities"].get(qid)
            if stored is None:
                pages.append({"ns": 0, "title": qid, "missing": True})
            else:
                pages.append(
                    {"ns": 0, "title": qid, "lastrevid": stored["lastrevid"], "length": 2000}
                )
        return 200, {"batchcomplete": True, "query": {"pages": pages}}
    if action == "wbgetclaims":
        stored = DATA["entities"].get(params.get("entity", ""), {})
        prop = params.get("property", "")
        return 200, {"claims": {prop: stored.get("claims", {}).get(prop, [])}}
    return 400, {"error": {"code": "badvalue", "info": f"Unrecognized value for action: {action}"}}
