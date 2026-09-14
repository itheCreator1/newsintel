import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class InputSection:
    kind: str
    reference_id: str | None
    start: int
    end: int


@dataclass(frozen=True)
class InputDocument:
    text: str
    fingerprint: str
    sections: tuple[InputSection, ...]


def _clean(value: str) -> str:
    return " ".join(value.split())


def build_input_document(
    *,
    title: str,
    body: str | None,
    descriptions: list[tuple[datetime, uuid.UUID, str]],
) -> InputDocument:
    values: list[tuple[str, str | None, str]] = [("title", None, _clean(title))]
    if body and _clean(body):
        values.append(("body", None, _clean(body)))
    else:
        seen: set[str] = set()
        for _, description_id, description in sorted(
            descriptions, key=lambda item: (item[0], item[1])
        ):
            cleaned = _clean(description)
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                values.append(("rss_description", str(description_id), cleaned))
    text_parts: list[str] = []
    sections: list[InputSection] = []
    offset = 0
    for kind, section_reference, value in values:
        if not value:
            continue
        if text_parts:
            offset += 2
        start = offset
        text_parts.append(value)
        offset += len(value)
        sections.append(InputSection(kind, section_reference, start, offset))
    text = "\n\n".join(text_parts)
    return InputDocument(text, hashlib.sha256(text.encode()).hexdigest(), tuple(sections))
