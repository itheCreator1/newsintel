"""Testing a feed address before it becomes a source: what it holds, or which feed a page links."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from app.feeds.fetching import FeedDocumentError, ParsedFeed, parse_feed_document
from app.feeds.http import FeedHttpResponse, FeedTooLarge
from app.feeds.network import UnsafeFeedUrl
from app.nlp.processors import ProcessorContext, detect_language

Fetch = Callable[[str], Awaitable[FeedHttpResponse]]
FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+xml"}
# Enough entries to tell the language; a feed with hundreds says no more.
LANGUAGE_SAMPLE = 50


class FeedProbeError(ValueError):
    pass


@dataclass(frozen=True)
class FeedProbe:
    url: str
    title: str | None
    entry_count: int
    language: str | None
    latest_entry_at: datetime | None
    # The page address the feed was found from, when the address given was not a feed.
    discovered_from: str | None
    candidates: list[tuple[str, str | None]] = field(default_factory=list)


class _FeedLinks(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__()
        self.base = base
        self.links: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values = {name.casefold(): value or "" for name, value in attrs}
        rel = values.get("rel", "").casefold().split()
        kind = values.get("type", "").casefold().split(";")[0].strip()
        href = values.get("href", "").strip()
        if "alternate" in rel and kind in FEED_TYPES and href:
            url = urljoin(self.base, href)
            if url not in (link for link, _ in self.links):
                self.links.append((url, values.get("title") or None))


def feed_links(content: bytes, base: str) -> list[tuple[str, str | None]]:
    """The RSS and Atom feeds a page announces with <link rel="alternate">, in page order."""
    parser = _FeedLinks(base)
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    return parser.links


async def _get(fetch: Fetch, url: str) -> FeedHttpResponse:
    try:
        response = await fetch(url)
    except UnsafeFeedUrl as exc:
        raise FeedProbeError("The address is not a public web address") from exc
    except FeedTooLarge as exc:
        raise FeedProbeError("The address answered with more than a feed may hold") from exc
    except httpx.TimeoutException as exc:
        raise FeedProbeError("The address did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise FeedProbeError("The address could not be reached") from exc
    if response.status_code != 200:
        raise FeedProbeError(f"The address answered HTTP {response.status_code}")
    return response


def _language(parsed: ParsedFeed) -> str | None:
    text = "\n".join(
        part
        for entry in parsed.entries[:LANGUAGE_SAMPLE]
        for part in (entry.title, entry.description)
        if part
    )
    context = ProcessorContext(
        text=text,
        input_fingerprint="",
        sections=(),
        language=None,
        stop_words=frozenset(),
        ner_enabled=False,
        ner_model="",
    )
    code = detect_language(context).language
    return None if code == "und" else code


def _probe(
    parsed: ParsedFeed,
    url: str,
    discovered_from: str | None,
    candidates: list[tuple[str, str | None]],
) -> FeedProbe:
    dates = [entry.published_at for entry in parsed.entries if entry.published_at]
    return FeedProbe(
        url=url,
        title=parsed.title,
        entry_count=len(parsed.entries),
        language=_language(parsed),
        latest_entry_at=max(dates) if dates else None,
        discovered_from=discovered_from,
        candidates=candidates,
    )


async def probe_feed(url: str, fetch: Fetch) -> FeedProbe:
    """Read the address as a feed; a web page is followed to the first feed it links."""
    response = await _get(fetch, url)
    try:
        return _probe(parse_feed_document(response.content, response.url), response.url, None, [])
    except FeedDocumentError:
        candidates = feed_links(response.content, response.url)
    if not candidates:
        raise FeedProbeError("This address is not a feed, and the page links to no feed")
    feed = await _get(fetch, candidates[0][0])
    try:
        parsed = parse_feed_document(feed.content, feed.url)
    except FeedDocumentError as exc:
        raise FeedProbeError("The feed this page links to is not RSS or Atom") from exc
    return _probe(parsed, feed.url, response.url, candidates)
