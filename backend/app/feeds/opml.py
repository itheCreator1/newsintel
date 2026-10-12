"""OPML, the outline format feed readers import and export subscription lists in.

Folders and `category` paths become tags. Non-standard `country` and `language` attributes carry
a source's codes, which other readers ignore.
"""

import xml.etree.ElementTree as StdlibET
from dataclasses import dataclass
from urllib.parse import urlsplit

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from app.feeds.schemas import MAX_TAG_LENGTH

_URL = TypeAdapter(AnyHttpUrl)


class OpmlError(ValueError):
    pass


@dataclass(frozen=True)
class OpmlEntry:
    name: str
    url: str
    tags: list[str]
    source_country: str | None
    expected_language: str | None


@dataclass(frozen=True)
class OpmlResult:
    entries: list[OpmlEntry]
    # Outlines with an address that is not http(s) or that an earlier outline already has.
    invalid: int


def _attribute(element: StdlibET.Element, name: str) -> str | None:
    value = next((v for k, v in element.attrib.items() if k.casefold() == name.casefold()), None)
    value = " ".join(value.split()) if value else None
    return value or None


def _add_tag(tags: list[str], value: str) -> None:
    tag = " ".join(value.split())[:MAX_TAG_LENGTH].strip()
    if tag and tag.casefold() not in {existing.casefold() for existing in tags}:
        tags.append(tag)


def _url(value: str) -> str | None:
    if urlsplit(value).scheme.casefold() not in ("http", "https"):
        return None
    try:
        return str(_URL.validate_python(value))
    except ValidationError:
        return None


def parse_opml(content: bytes) -> OpmlResult:
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise OpmlError("The file could not be read as XML") from exc
    body = next((child for child in root if child.tag.casefold() == "body"), None)
    if root.tag.casefold() != "opml" or body is None:
        raise OpmlError("This is not an OPML file")

    entries: list[OpmlEntry] = []
    seen: set[str] = set()
    invalid = 0

    def walk(parent: StdlibET.Element, folders: list[str]) -> None:
        nonlocal invalid
        for outline in parent:
            if outline.tag.casefold() != "outline":
                continue
            label = _attribute(outline, "text") or _attribute(outline, "title")
            address = _attribute(outline, "xmlUrl")
            if address is None:
                walk(outline, [*folders, label] if label else folders)
                continue
            url = _url(address)
            if url is None or url in seen:
                invalid += 1
                continue
            seen.add(url)
            tags: list[str] = []
            for folder in folders:
                _add_tag(tags, folder)
            for category in (_attribute(outline, "category") or "").split(","):
                _add_tag(tags, category.strip().strip("/"))
            country = (_attribute(outline, "country") or "").upper()
            language = (_attribute(outline, "language") or "").lower()
            entries.append(
                OpmlEntry(
                    name=(label or urlsplit(url).hostname or url)[:200],
                    url=url,
                    tags=tags,
                    source_country=country if len(country) == 2 and country.isalpha() else None,
                    expected_language=language if 2 <= len(language) <= 16 else None,
                )
            )

    walk(body, [])
    return OpmlResult(entries, invalid)


def render_opml(entries: list[OpmlEntry], *, title: str) -> bytes:
    root = StdlibET.Element("opml", version="2.0")
    head = StdlibET.SubElement(root, "head")
    StdlibET.SubElement(head, "title").text = title
    body = StdlibET.SubElement(root, "body")
    for entry in entries:
        attributes = {"type": "rss", "text": entry.name, "title": entry.name, "xmlUrl": entry.url}
        if entry.tags:
            attributes["category"] = ",".join(f"/{tag.replace(',', ' ')}" for tag in entry.tags)
        if entry.source_country:
            attributes["country"] = entry.source_country
        if entry.expected_language:
            attributes["language"] = entry.expected_language
        StdlibET.SubElement(body, "outline", attributes)
    StdlibET.indent(root)
    declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    return declaration + StdlibET.tostring(root, encoding="unicode").encode() + b"\n"
