import html
import re
import xml.etree.ElementTree as StdlibET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]


class FeedDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedEntry:
    guid: str | None
    title: str
    url: str
    description: str | None
    published_at: datetime | None
    metadata: dict[str, str]


@dataclass(frozen=True)
class ParsedFeed:
    title: str | None
    entries: list[ParsedEntry]
    invalid_entries: int


def _name(element: StdlibET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].casefold()


def _text(element: StdlibET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _child(element: StdlibET.Element, name: str) -> StdlibET.Element | None:
    return next((child for child in element if _name(child) == name), None)


def _plain_text(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", html.unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = (
            parsedate_to_datetime(value)
            if "," in value
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        return parsed if parsed.tzinfo else parsed.astimezone()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_feed_document(content: bytes, feed_url: str) -> ParsedFeed:
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise FeedDocumentError("Malformed XML feed") from exc
    kind = _name(root)
    if kind == "rss":
        container = _child(root, "channel")
        if container is None:
            raise FeedDocumentError("RSS document has no channel")
        nodes = [node for node in container if _name(node) == "item"]
        base = _text(_child(container, "link")) or feed_url
    elif kind == "feed":
        container = root
        nodes = [node for node in root if _name(node) == "entry"]
        alternate = next(
            (
                node
                for node in root
                if _name(node) == "link" and node.attrib.get("rel", "alternate") == "alternate"
            ),
            None,
        )
        base = alternate.attrib.get("href", feed_url) if alternate is not None else feed_url
    else:
        raise FeedDocumentError("Document is not RSS or Atom")

    entries: list[ParsedEntry] = []
    invalid = 0
    for node in nodes:
        title = _text(_child(node, "title"))
        link_node = _child(node, "link")
        link = link_node.attrib.get("href") if link_node is not None else None
        link = link or _text(link_node)
        if not title or not link:
            invalid += 1
            continue
        description = (
            _text(_child(node, "description"))
            or _text(_child(node, "summary"))
            or _text(_child(node, "content"))
        )
        published = (
            _text(_child(node, "pubdate"))
            or _text(_child(node, "published"))
            or _text(_child(node, "updated"))
        )
        guid = _text(_child(node, "guid")) or _text(_child(node, "id"))
        entries.append(
            ParsedEntry(
                guid, title, urljoin(base, link), _plain_text(description), _date(published), {}
            )
        )
    return ParsedFeed(_text(_child(container, "title")), entries, invalid)
