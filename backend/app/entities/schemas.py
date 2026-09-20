import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.feeds.schemas import ArticleResponse


class MentionTimelineDay(BaseModel):
    date: date
    mentions: int


class EntityDossierResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    normalized_text: str
    language: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    aliases_status: Literal["unavailable"] = "unavailable"
    total_mentions: int
    article_count: int
    cluster_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    timeline_days: int
    timeline: list[MentionTimelineDay]


class EntityArticlePage(BaseModel):
    items: list[ArticleResponse]
    next_cursor: str | None


class EntityClusterResponse(BaseModel):
    id: uuid.UUID
    article_count: int
    source_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    representative_article: ArticleResponse | None


class EntityClusterPage(BaseModel):
    items: list[EntityClusterResponse]
    next_cursor: str | None


class RelatedEntityResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    normalized_text: str
    language: str
    entity_type: str
    article_count: int


class RelatedCountryResponse(BaseModel):
    country_code: str
    role: str
    article_count: int


class RelatedFeedResponse(BaseModel):
    id: uuid.UUID
    name: str
    article_count: int


class EntityRelationshipsResponse(BaseModel):
    window_days: int
    entities: list[RelatedEntityResponse]
    countries: list[RelatedCountryResponse]
    feeds: list[RelatedFeedResponse]
