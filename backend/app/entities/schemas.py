import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.feeds.schemas import ArticleResponse


class MentionTimelineDay(BaseModel):
    date: date
    mentions: int


EntityStatus = Literal["provisional", "established"]


class EntityDossierResponse(BaseModel):
    id: uuid.UUID
    # The id asked for, when it was a variant: the dossier is always its root's.
    redirected_from: uuid.UUID | None = None
    display_name: str
    normalized_text: str
    language: str
    entity_type: str
    preferred_text: str | None = None
    status: EntityStatus = "provisional"
    ambiguous: bool = False
    note: str | None = None
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


class EntityAuthorityResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    preferred_text: str | None
    status: EntityStatus
    ambiguous: bool
    note: str | None
    authority_id: uuid.UUID | None


class EntityAuthorityUpdate(BaseModel):
    """Only the fields sent change; null clears the preferred name or the note."""

    model_config = ConfigDict(extra="forbid")

    preferred_text: str | None = Field(default=None, max_length=500)
    status: EntityStatus | None = None
    ambiguous: bool | None = None
    note: str | None = Field(default=None, max_length=4000)


class EntityMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: uuid.UUID


class EntityAuthorityRunResponse(BaseModel):
    """The batched run that moves the article links; it advances on every scheduler cycle."""

    id: uuid.UUID
    kind: Literal["merge", "split"]
    status: Literal["running", "finished", "failed"]
    entity_id: uuid.UUID
    root_id: uuid.UUID


class EntityVariantResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    normalized_text: str
    language: str
    entity_type: str


class EntityVariantList(BaseModel):
    items: list[EntityVariantResponse]


class EntityHistoryItem(BaseModel):
    id: uuid.UUID
    action: str
    entity_id: uuid.UUID
    other_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


class EntityHistory(BaseModel):
    items: list[EntityHistoryItem]
