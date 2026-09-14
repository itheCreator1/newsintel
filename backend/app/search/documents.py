import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import Any


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", value))).strip()


@dataclass(frozen=True)
class ProvenanceDocument:
    source_id: uuid.UUID
    source_name: str
    source_country: str | None

    def to_payload(self) -> dict[str, str | None]:
        return {
            "source_id": str(self.source_id),
            "source_name": self.source_name,
            "source_country": self.source_country.upper() if self.source_country else None,
        }


@dataclass(frozen=True)
class ArticleDocument:
    article_id: uuid.UUID
    title: str
    descriptions: list[str]
    body: str | None
    published_at: datetime | None
    first_discovered_at: datetime
    content_available: bool
    processing_status: str | None
    provenance: list[ProvenanceDocument]

    def to_index_payload(self) -> dict[str, Any]:
        sources = {item.source_id for item in self.provenance}
        return {
            "article_id": str(self.article_id),
            "title": self.title,
            "descriptions": [plain_text(value) for value in self.descriptions if value],
            "body": self.body,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "first_discovered_at": self.first_discovered_at.isoformat(),
            "effective_date": (self.published_at or self.first_discovered_at).isoformat(),
            "content_available": self.content_available,
            "processing_status": self.processing_status,
            "distinct_source_count": len(sources),
            "provenance": [item.to_payload() for item in self.provenance],
        }


ARTICLE_INDEX_SETTINGS: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "analysis": {
            "normalizer": {
                "lowercase_normalizer": {
                    "type": "custom",
                    "filter": ["lowercase"],
                }
            }
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "article_id": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "standard"},
            "descriptions": {"type": "text", "analyzer": "standard"},
            "body": {"type": "text", "analyzer": "standard"},
            "published_at": {"type": "date"},
            "first_discovered_at": {"type": "date"},
            "effective_date": {"type": "date"},
            "content_available": {"type": "boolean"},
            "processing_status": {"type": "keyword"},
            "distinct_source_count": {"type": "integer"},
            "provenance": {
                "type": "nested",
                "properties": {
                    "source_id": {"type": "keyword"},
                    "source_name": {
                        "type": "keyword",
                        "normalizer": "lowercase_normalizer",
                    },
                    "source_country": {"type": "keyword"},
                },
            },
        },
    },
}
