import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.articles.schemas import ArticleResponse


class MentionTimelineDay(BaseModel):
    date: date
    mentions: int


EntityStatus = Literal["provisional", "established"]
# app.entities.models.ENTITY_TYPES, spelled out so the API contract lists them.
EntityType = Literal["PERSON", "ORG", "GPE", "LOCATION", "EVENT", "PRODUCT", "TOPIC", "OTHER"]


class EntityNameRef(BaseModel):
    id: uuid.UUID
    display_name: str
    entity_type: str
    normalized_text: str


class EntityDossierResponse(BaseModel):
    id: uuid.UUID
    # The id asked for, when it was a variant: the dossier is always its root's.
    redirected_from: uuid.UUID | None = None
    display_name: str
    normalized_text: str
    language: str
    # The type to show: the user's when they picked one, else the model's (ner_type).
    entity_type: str
    ner_type: str
    hidden: bool = False
    preferred_text: str | None = None
    status: EntityStatus = "provisional"
    ambiguous: bool = False
    note: str | None = None
    # The names this root takes every type of, and other roots a claim of one of its names
    # would bring in ("AI" the PERSON beside this "AI" the ORG).
    claims: list[str] = Field(default_factory=list)
    other_types: list[EntityNameRef] = Field(default_factory=list)
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


class EntityUsage(BaseModel):
    """Watches and saved searches that filter on the root or one of its names."""

    monitors: int
    saved_searches: int


class EntityAuthorityResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    preferred_text: str | None
    entity_type: str
    ner_type: str
    hidden: bool
    status: EntityStatus
    ambiguous: bool
    note: str | None
    authority_id: uuid.UUID | None


class EntityAuthorityUpdate(BaseModel):
    """Only the fields sent change; null clears the preferred name, the type or the note.

    A type or hidden change reindexes the root's articles, as a new name does.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_text: str | None = Field(default=None, max_length=500)
    entity_type: EntityType | None = None
    hidden: bool | None = None
    status: EntityStatus | None = None
    ambiguous: bool | None = None
    note: str | None = Field(default=None, max_length=4000)


class EntityMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: uuid.UUID


class EntityAuthorityRunResponse(BaseModel):
    """The batched run that moves the article links, or reindexes a root's articles after a
    change of type or of hidden; it advances on every scheduler cycle."""

    id: uuid.UUID
    kind: Literal["merge", "split", "reindex"]
    status: Literal["running", "finished", "failed"]
    entity_id: uuid.UUID
    root_id: uuid.UUID


class EntityClaimRequest(BaseModel):
    """The name to claim every type of: one of the root's own; omitted, the root's own key."""

    model_config = ConfigDict(extra="forbid")

    normalized_text: str | None = Field(default=None, min_length=1, max_length=1000)


class EntityClaimRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_text: str = Field(min_length=1, max_length=1000)


class EntityClaimSkipped(BaseModel):
    entity_id: uuid.UUID
    display_name: str
    entity_type: str
    reason: str


class EntityClaimResponse(BaseModel):
    """The merge runs the claim started, and the names it left where they were, with why."""

    runs: list["EntityAuthorityRunResponse"]
    skipped: list[EntityClaimSkipped]


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
    hidden: bool
    variant_count: int
    # Articles that name it now, under any of its names once a merge run has moved them.
    article_count: int
    # The Wikidata item the root is linked to, if any.
    qid: str | None


class AuthorityBulkRequest(BaseModel):
    """One change to several roots at once, all or nothing.

    merge: every id becomes a name of `target_id`'s root. set_type: `entity_type` (null goes
    back to the model's type). set_status: `status`. hide, show: not a name, or a name again.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["merge", "set_type", "set_status", "hide", "show"]
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    target_id: uuid.UUID | None = None
    entity_type: EntityType | None = None
    status: EntityStatus | None = None

    @model_validator(mode="after")
    def _needs(self) -> "AuthorityBulkRequest":
        if self.action == "merge" and self.target_id is None:
            raise ValueError("merge needs target_id")
        if self.action == "set_type" and "entity_type" not in self.model_fields_set:
            raise ValueError("set_type needs entity_type (null for the model's own)")
        if self.action == "set_status" and self.status is None:
            raise ValueError("set_status needs status")
        return self


class AuthorityBulkResponse(BaseModel):
    """The runs the change started: merges, or reindexes of each root's articles."""

    runs: list[EntityAuthorityRunResponse]


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
