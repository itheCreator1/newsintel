"""The only code that talks to Wikidata: four read-only Action API calls through one throttle.

Every request names the app and its owner in the User-Agent, asks for gzip and sets maxlag=5.
A failed request is never retried on the spot: the throttle records a pause and the caller's run
stops, to try again in a later cycle. Read the rules in throttle.py.
"""

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType
from typing import Any

import httpx

from app.core.config import Settings
from app.wikidata.errors import WikidataDisabled, WikidataUnavailable
from app.wikidata.parsing import (
    MULTILINGUAL,
    Item,
    PageInfo,
    api_error,
    claim_item_ids,
    is_qid,
    parse_entities,
    parse_info,
    parse_search,
)
from app.wikidata.throttle import Outcome, Throttle

# The Action API's own limit for ids or titles in one request (Wikidata:Data access).
BATCH = 50
SEARCH_LIMIT = 7
LIGHT_PROPS = "labels|aliases|descriptions|sitelinks|info"
FULL_PROPS = "labels|aliases|descriptions|claims|sitelinks|info"
EMAIL = re.compile(r"[^@\s()<>]+@[^@\s()<>]+\.[^@\s()<>]+")
URL = re.compile(r"https?://[^\s()<>/]+\.[^\s()<>]+(/[^\s()<>]*)?")


def _app_version() -> str:
    try:
        return version("newsintel-backend")
    except PackageNotFoundError:
        return "0"


def contact_ok(contact: str) -> bool:
    """An email address or an http(s) URL: the User-Agent policy wants a way to reach us."""
    contact = contact.strip()
    return bool(EMAIL.fullmatch(contact) or URL.fullmatch(contact))


def user_agent(contact: str) -> str:
    """Wikimedia's form: <client>/<version> (<contact>) <library>/<version>."""
    return f"NewsIntel/{_app_version()} ({contact.strip()}) python-httpx/{httpx.__version__}"


def _retry_after(response: httpx.Response, now: datetime) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def _unique_qids(qids: Iterable[str]) -> list[str]:
    found: list[str] = []
    for qid in qids:
        if not is_qid(qid):
            raise ValueError(f"Not a Wikidata item id: {qid!r}")
        if qid not in found:
            found.append(qid)
    return found


class WikidataClient:
    def __init__(
        self,
        settings: Settings,
        throttle: Throttle,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.throttle = throttle
        self.languages = tuple(settings.wikidata_languages)
        self.enabled = settings.wikidata_enabled and contact_ok(settings.wikidata_contact)
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=settings.wikidata_timeout_seconds,
            follow_redirects=False,
            headers={
                "User-Agent": user_agent(settings.wikidata_contact),
                "Accept-Encoding": "gzip",
            },
        )

    async def __aenter__(self) -> "WikidataClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def _read(self, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self.settings.wikidata_max_response_bytes:
                raise ValueError("the answer is larger than wikidata_max_response_bytes")
        return bytes(body)

    async def _get(self, kind: str, params: dict[str, str]) -> dict[str, Any]:
        """One request through the throttle; any failure records a pause and raises."""
        if not self.enabled:
            raise WikidataDisabled(
                "Wikidata is off: set NEWSINTEL_WIKIDATA_CONTACT to an email or URL"
                if self.settings.wikidata_enabled
                else "Wikidata is switched off (NEWSINTEL_WIKIDATA_ENABLED=false)"
            )
        query = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        async with self.throttle.slot(kind) as slot:

            def fail(outcome: Outcome, detail: str, retry_after: float | None = None) -> None:
                slot.record(outcome, retry_after)
                raise WikidataUnavailable(outcome, detail)

            retry_after: float | None = None
            try:
                async with self._http.stream(
                    "GET", self.settings.wikidata_url, params=query
                ) as response:
                    retry_after = _retry_after(response, slot.now())
                    if response.status_code in (429, 503):
                        fail("rate_limited", f"HTTP {response.status_code}", retry_after)
                    if response.status_code == 403:
                        fail("blocked", "HTTP 403: the User-Agent or address may be blocked")
                    if response.status_code != 200:
                        fail("error", f"HTTP {response.status_code}", retry_after)
                    raw = await self._read(response)
            except httpx.HTTPError as exc:
                fail("error", f"{type(exc).__name__}: {exc}")
            except ValueError as exc:
                fail("error", str(exc))
            try:
                body = httpx.Response(200, content=raw).json()
            except ValueError:
                fail("error", "the answer is not JSON")
            error = api_error(body)
            if error is not None:
                fail("error", f"{error[0]}: {error[1]}", retry_after)
            slot.record("ok")
            return body  # type: ignore[no-any-return]

    async def search(self, text: str, language: str) -> list[str]:
        """Item QIDs whose label or alias matches `text` in `language`, best first."""
        body = await self._get(
            "search",
            {
                "action": "wbsearchentities",
                "search": text,
                "language": language,
                "uselang": language,
                "type": "item",
                "limit": str(SEARCH_LIMIT),
            },
        )
        return parse_search(body)

    async def info(self, qids: Iterable[str]) -> dict[str, PageInfo]:
        """Each item's revision and size, 50 to a request: the cheap check before a refetch."""
        found: dict[str, PageInfo] = {}
        for batch in _chunks(_unique_qids(qids), BATCH):
            body = await self._get(
                "info",
                {"action": "query", "prop": "info", "redirects": "1", "titles": "|".join(batch)},
            )
            found.update(parse_info(body, requested=batch))
        return found

    async def _entities(self, batch: list[str], props: str) -> list[Item]:
        body = await self._get(
            "entities",
            {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": props,
                "languages": "|".join((*self.languages, MULTILINGUAL)),
            },
        )
        return parse_entities(body, languages=self.languages)

    async def items(self, qids: Iterable[str], *, full: bool = False) -> list[Item]:
        """The items, light (no claims: a candidate) or full (with claims: a linked item).

        A full fetch asks for the sizes first and keeps each request's summed size under
        wikidata_batch_bytes: a country's claims run to megabytes. Missing and redirected items
        found by that check are reported, and a redirect's target is fetched instead.
        """
        wanted = _unique_qids(qids)
        if not full:
            light: list[Item] = []
            for batch in _chunks(wanted, BATCH):
                light.extend(await self._entities(batch, LIGHT_PROPS))
            return light
        pages = await self.info(wanted)
        found: list[Item] = []
        fetch: list[PageInfo] = []
        for qid in wanted:
            page = pages[qid]
            if page.state == "missing":
                found.append(Item(qid=qid, state="missing", claims_fetched=True))
            elif page.state == "redirected" and page.redirect_to is not None:
                found.append(
                    Item(
                        qid=qid,
                        state="redirected",
                        redirect_to=page.redirect_to,
                        claims_fetched=True,
                    )
                )
                target = pages.get(page.redirect_to)
                if target is not None and target.state == "ok":
                    fetch.append(target)
            else:
                fetch.append(page)
        batches: list[list[str]] = []
        size = 0
        for page in fetch:
            if any(page.qid in batch for batch in batches):
                continue
            length = page.length or 0
            if (
                not batches
                or len(batches[-1]) >= BATCH
                or (size + length > self.settings.wikidata_batch_bytes and batches[-1])
            ):
                batches.append([])
                size = 0
            batches[-1].append(page.qid)
            size += length
        for batch in batches:
            found.extend(
                item
                for item in await self._entities(batch, FULL_PROPS)
                if item.qid not in {done.qid for done in found}
            )
        return found

    async def claim_items(self, qid: str, prop: str) -> list[str]:
        """One property's item values on one item (the type check of a close call)."""
        (qid,) = _unique_qids([qid])
        if not re.fullmatch(r"P[1-9][0-9]*", prop):
            raise ValueError(f"Not a Wikidata property id: {prop!r}")
        body = await self._get(
            "claims", {"action": "wbgetclaims", "entity": qid, "property": prop, "props": ""}
        )
        return claim_item_ids(body.get("claims"), prop)
