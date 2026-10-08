"""Read the Action API's answers (format=json, formatversion=2) into the few fields we keep.

Pure functions: no I/O, so the tests run them on recorded answers.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

State = Literal["ok", "redirected", "missing"]

QID = re.compile(r"Q[1-9][0-9]*")
# Wikidata properties: external identifiers (MARC 024 $2) and the two item-valued ones we read.
ID_PROPERTIES = {"P214": "viaf", "P213": "isni", "P244": "lcnaf"}
INSTANCE_OF = "P31"
SUBCLASS_OF = "P279"
DIFFERENT_FROM = "P1889"


def is_qid(text: str) -> bool:
    return QID.fullmatch(text) is not None


@dataclass(frozen=True)
class Item:
    qid: str
    state: State
    revision: int | None = None
    labels: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    instance_of: list[str] = field(default_factory=list)
    subclass_of: list[str] = field(default_factory=list)
    different_from: list[str] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    sitelinks: int = 0
    redirect_to: str | None = None
    # False for a light fetch: the claim fields above are then unknown, not empty.
    claims_fetched: bool = False


@dataclass(frozen=True)
class PageInfo:
    qid: str
    revision: int | None
    length: int | None
    state: State
    redirect_to: str | None


def api_error(body: Any) -> tuple[str, str] | None:
    """The (code, text) of an API error, in either error format; None for a good answer.

    A maxlag error comes back with HTTP 200, so the body is always read.
    """
    if not isinstance(body, dict):
        return ("unreadable", "the answer is not a JSON object")
    error = body.get("error")
    if isinstance(error, dict):
        return (str(error.get("code", "unknown")), str(error.get("info", "")))
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        return (str(first.get("code", "unknown")), str(first.get("text", first.get("info", ""))))
    return None


def _texts(values: Any, languages: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    return {
        language: value["value"]
        for language, value in values.items()
        if language in languages and isinstance(value, dict) and "value" in value
    }


def _aliases(values: Any, languages: tuple[str, ...]) -> dict[str, list[str]]:
    if not isinstance(values, dict):
        return {}
    found: dict[str, list[str]] = {}
    for language, entries in values.items():
        if language not in languages or not isinstance(entries, list):
            continue
        names = [
            entry["value"] for entry in entries if isinstance(entry, dict) and "value" in entry
        ]
        if names:
            found[language] = names
    return found


def _values(statements: Any) -> list[Any]:
    """A property's values, preferred statements first; deprecated and no-value ones dropped."""
    if not isinstance(statements, list):
        return []
    ranked = sorted(
        (
            statement
            for statement in statements
            if isinstance(statement, dict) and statement.get("rank") != "deprecated"
        ),
        key=lambda statement: statement.get("rank") != "preferred",
    )
    values = []
    for statement in ranked:
        snak = statement.get("mainsnak") or {}
        if snak.get("snaktype") != "value":
            continue
        value = (snak.get("datavalue") or {}).get("value")
        if value is not None:
            values.append(value)
    return values


def _item_ids(statements: Any) -> list[str]:
    ids: list[str] = []
    for value in _values(statements):
        qid = value.get("id") if isinstance(value, dict) else None
        if isinstance(qid, str) and is_qid(qid) and qid not in ids:
            ids.append(qid)
    return ids


def claim_item_ids(claims: Any, prop: str) -> list[str]:
    return _item_ids(claims.get(prop) if isinstance(claims, dict) else None)


def _revision(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _item(entity: dict[str, Any], qid: str, languages: tuple[str, ...]) -> Item:
    claims = entity.get("claims")
    ids: dict[str, str] = {}
    if isinstance(claims, dict):
        for prop, scheme in ID_PROPERTIES.items():
            strings = [value for value in _values(claims.get(prop)) if isinstance(value, str)]
            if strings:
                ids[scheme] = strings[0]
    sitelinks = entity.get("sitelinks")
    return Item(
        qid=qid,
        state="ok",
        revision=_revision(entity.get("lastrevid")),
        labels=_texts(entity.get("labels"), languages),
        aliases=_aliases(entity.get("aliases"), languages),
        descriptions=_texts(entity.get("descriptions"), languages),
        instance_of=claim_item_ids(claims, INSTANCE_OF),
        subclass_of=claim_item_ids(claims, SUBCLASS_OF),
        different_from=claim_item_ids(claims, DIFFERENT_FROM),
        ids=ids,
        sitelinks=len(sitelinks) if isinstance(sitelinks, dict) else 0,
        claims_fetched=isinstance(claims, dict),
    )


def parse_entities(body: dict[str, Any], *, languages: tuple[str, ...]) -> list[Item]:
    """wbgetentities: one Item per entity, plus a "redirected" Item for each redirect's source."""
    items: dict[str, Item] = {}
    entities = body.get("entities")
    if not isinstance(entities, dict):
        return []
    for key, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        qid = str(entity.get("id")) if is_qid(str(entity.get("id"))) else str(key)
        if not is_qid(qid):
            continue
        if "missing" in entity:
            items[qid] = Item(qid=qid, state="missing")
            continue
        redirect = entity.get("redirects")
        if isinstance(redirect, dict) and is_qid(str(redirect.get("from"))):
            source = str(redirect["from"])
            items[source] = Item(qid=source, state="redirected", redirect_to=qid)
        items[qid] = _item(entity, qid, languages)
    return list(items.values())


def parse_search(body: dict[str, Any]) -> list[str]:
    """wbsearchentities: the QIDs found, best first."""
    results = body.get("search")
    if not isinstance(results, list):
        return []
    found: list[str] = []
    for result in results:
        qid = result.get("id") if isinstance(result, dict) else None
        if isinstance(qid, str) and is_qid(qid) and qid not in found:
            found.append(qid)
    return found


def parse_info(body: dict[str, Any], *, requested: list[str]) -> dict[str, PageInfo]:
    """action=query&prop=info: revision and size per item; redirects and missing items marked."""
    query = body.get("query")
    if not isinstance(query, dict):
        return {}
    found: dict[str, PageInfo] = {}
    for redirect in query.get("redirects") or []:
        if isinstance(redirect, dict) and is_qid(str(redirect.get("from"))):
            source, target = str(redirect["from"]), str(redirect.get("to"))
            if is_qid(target):
                found[source] = PageInfo(source, None, None, "redirected", target)
    for page in query.get("pages") or []:
        title = page.get("title") if isinstance(page, dict) else None
        if not isinstance(title, str) or not is_qid(title):
            continue
        if page.get("missing") is not None and page.get("missing") is not False:
            found[title] = PageInfo(title, None, None, "missing", None)
            continue
        found[title] = PageInfo(
            title, _revision(page.get("lastrevid")), _revision(page.get("length")), "ok", None
        )
    # A requested id the answer left out counts as missing, so nothing waits on it forever.
    for qid in requested:
        found.setdefault(qid, PageInfo(qid, None, None, "missing", None))
    return found
