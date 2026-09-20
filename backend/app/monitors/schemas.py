import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, model_validator

from app.investigations.schemas import InvestigationState, Name
from app.search.schemas import SearchResult

MonitorKind = Literal["search", "entity", "source", "country", "cluster"]


def validate_target(kind: MonitorKind, state: InvestigationState) -> None:
    """A monitor's kind names what it watches; the state must actually point at it."""
    if kind == "search":
        if not state.model_dump(exclude={"sort", "interval"}, exclude_defaults=True):
            raise ValueError("A search monitor needs a query or at least one filter")
    elif kind == "entity":
        if not state.entity_id:
            raise ValueError("An entity monitor needs at least one entity_id")
    elif kind == "source":
        if not state.source_id:
            raise ValueError("A source monitor needs at least one source_id")
    elif kind == "country":
        if not (state.story_country or state.mentioned_country or state.source_country):
            raise ValueError("A country monitor needs a story, mentioned or source country")
    elif len(state.story_cluster_id) != 1:
        raise ValueError("A cluster monitor needs exactly one story_cluster_id")


class MonitorCreate(BaseModel):
    name: Name
    kind: MonitorKind
    state: InvestigationState

    @model_validator(mode="after")
    def _names_its_target(self) -> Self:
        validate_target(self.kind, self.state)
        return self


class MonitorUpdate(BaseModel):
    name: Name | None = None
    enabled: bool | None = None
    kind: MonitorKind | None = None
    state: InvestigationState | None = None

    @model_validator(mode="after")
    def _target_changes_as_a_pair(self) -> Self:
        if (self.kind is None) != (self.state is None):
            raise ValueError("kind and state must be changed together")
        if self.kind is not None and self.state is not None:
            validate_target(self.kind, self.state)
        return self


class MonitorResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: MonitorKind
    enabled: bool
    state_version: int
    state: InvestigationState | None
    problem: str | None
    unseen_article_count: int
    unseen_cluster_count: int
    evaluated_through: datetime | None
    viewed_through: datetime | None
    latest_match_at: datetime | None
    latest_match_article_id: uuid.UUID | None
    last_evaluated_at: datetime | None
    next_evaluation_at: datetime
    error_category: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class MonitorPage(BaseModel):
    items: list[MonitorResponse]
    next_cursor: str | None


class MonitorViewed(BaseModel):
    through: AwareDatetime


class MonitorResultPage(BaseModel):
    items: list[SearchResult]
    next_cursor: str | None
    window_start: datetime | None
    window_end: datetime | None


class ChangeEvidence(BaseModel):
    article_id: uuid.UUID
    title: str


class SourceChange(BaseModel):
    source_id: uuid.UUID
    name: str
    article_count: int
    evidence: list[ChangeEvidence]


class EntityChange(BaseModel):
    entity_id: uuid.UUID
    name: str
    entity_type: str
    article_count: int
    evidence: list[ChangeEvidence]


class StoryChange(BaseModel):
    cluster_id: uuid.UUID
    title: str | None
    status: Literal["new", "grew"]
    article_count: int
    source_count: int
    sources_added: int
    evidence: list[ChangeEvidence]


class MonitorChanges(BaseModel):
    """What `(window_start, window_end]` brought that the monitor had not matched before."""

    window_start: datetime | None
    window_end: datetime | None
    article_count: int
    sources: list[SourceChange]
    entities: list[EntityChange]
    stories: list[StoryChange]
    more_sources: bool
    more_entities: bool
    more_stories: bool
