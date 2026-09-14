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
class EntityDocument:
    entity_id: uuid.UUID
    entity_type: str
    text: str
    normalized_text: str

    def to_payload(self) -> dict[str, str]:
        return {
            "id": str(self.entity_id),
            "type": self.entity_type,
            "text": self.text,
            "normalized_text": self.normalized_text,
        }


@dataclass(frozen=True)
class KeywordDocument:
    keyword_id: uuid.UUID
    kind: str
    text: str
    normalized_text: str

    def to_payload(self) -> dict[str, str]:
        return {
            "id": str(self.keyword_id),
            "kind": self.kind,
            "text": self.text,
            "normalized_text": self.normalized_text,
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
    detected_language: str | None = None
    entities: list[EntityDocument] | None = None
    keywords: list[KeywordDocument] | None = None
    primary_story_country: str | None = None
    mentioned_countries: list[str] | None = None

    def to_index_payload(self, *, schema_version: int = 1) -> dict[str, Any]:
        sources = {item.source_id for item in self.provenance}
        payload: dict[str, Any] = {
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
        if schema_version >= 2:
            entities = self.entities or []
            keywords = self.keywords or []
            payload.update(
                {
                    "detected_language": self.detected_language,
                    "entities": [item.to_payload() for item in entities],
                    "entity_text": [item.text for item in entities],
                    "keywords": [item.to_payload() for item in keywords],
                    "keyword_ids": [str(item.keyword_id) for item in keywords],
                    "keyword_text": [item.text for item in keywords],
                    "primary_story_country": self.primary_story_country,
                    "mentioned_countries": sorted(set(self.mentioned_countries or [])),
                }
            )
        return payload


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

ARTICLE_INDEX_SETTINGS_V2: dict[str, Any] = {
    "settings": ARTICLE_INDEX_SETTINGS["settings"],
    "mappings": {
        "dynamic": "strict",
        "properties": {
            **ARTICLE_INDEX_SETTINGS["mappings"]["properties"],
            "detected_language": {"type": "keyword"},
            "entity_text": {"type": "text", "analyzer": "standard"},
            "keyword_text": {"type": "text", "analyzer": "standard"},
            "keyword_ids": {"type": "keyword"},
            "primary_story_country": {"type": "keyword"},
            "mentioned_countries": {"type": "keyword"},
            "entities": {
                "type": "nested",
                "properties": {
                    "id": {"type": "keyword"},
                    "type": {"type": "keyword"},
                    "text": {"type": "text", "analyzer": "standard"},
                    "normalized_text": {
                        "type": "keyword",
                        "normalizer": "lowercase_normalizer",
                    },
                },
            },
            "keywords": {
                "type": "object",
                "properties": {
                    "id": {"type": "keyword"},
                    "kind": {"type": "keyword"},
                    "text": {"type": "text", "analyzer": "standard"},
                    "normalized_text": {
                        "type": "keyword",
                        "normalizer": "lowercase_normalizer",
                    },
                },
            },
        },
    },
}
