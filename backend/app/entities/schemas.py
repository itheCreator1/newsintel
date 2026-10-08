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


class AuthorityRootResponse(BaseModel):
    """A root of the authority file, with how many other names point at it."""

    id: uuid.UUID
    display_name: str
    entity_type: str
    language: str
    status: EntityStatus
    ambiguous: bool
    variant_count: int


class AuthorityRootPage(BaseModel):
    items: list[AuthorityRootResponse]
    next_cursor: str | None


class AuthorityNameResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    entity_type: str
    article_count: int


class AuthoritySuggestionResponse(BaseModel):
    """Maybe the same: approve by merging the variant into the root, reject by marking distinct."""

    root: AuthorityNameResponse
    variant: AuthorityNameResponse
    score: float
    reasons: list[str]
    shared_articles: int


class AuthoritySuggestionList(BaseModel):
    items: list[AuthoritySuggestionResponse]


class AuthorityHistoryItem(EntityHistoryItem):
    entity_name: str | None
    other_name: str | None


class AuthorityHistoryPage(BaseModel):
    items: list[AuthorityHistoryItem]
    next_cursor: str | None


SeeAlsoLabel = Literal[
    "later_name",
    "earlier_name",
    "part_of",
    "has_part",
    "member_of",
    "has_member",
    "leader_of",
    "led_by",
    "related",
]


class SeeAlsoEntity(BaseModel):
    id: uuid.UUID
    display_name: str
    entity_type: str


class SeeAlsoSource(BaseModel):
    id: uuid.UUID
    title: str


class SeeAlsoItem(BaseModel):
    """One link, labelled from the side of the entity asked about ("earlier_name": Facebook)."""

    id: uuid.UUID
    label: SeeAlsoLabel
    entity: SeeAlsoEntity
    valid_from: str | None
    valid_to: str | None
    note: str | None
    source_article: SeeAlsoSource | None


class SeeAlsoResponse(BaseModel):
    # The labels this entity's type can take from its own side, for the "Add link" form.
    labels: list[SeeAlsoLabel]
    items: list[SeeAlsoItem]


class SeeAlsoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: SeeAlsoLabel
    target_id: uuid.UUID
    valid_from: str | None = Field(default=None, max_length=10)
    valid_to: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=4000)
    source_article_id: uuid.UUID | None = None


class SeeAlsoUpdate(BaseModel):
    """Only the fields sent change; null clears one."""

    model_config = ConfigDict(extra="forbid")

    valid_from: str | None = Field(default=None, max_length=10)
    valid_to: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=4000)
    source_article_id: uuid.UUID | None = None
