import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.search.query import SearchSyntaxError, parse_query
from app.search.timeline import RequestedInterval

STATE_VERSION = 1
CountryCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{2}$")
]
LanguageCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[A-Za-z][A-Za-z-]{1,15}$"),
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class InvestigationState(BaseModel):
    """Complete search investigation, using the /search query parameter names."""

    model_config = ConfigDict(extra="forbid")

    q: str = Field(default="", max_length=2000)
    source_id: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    source_country: list[CountryCode] = Field(default_factory=list, max_length=50)
    after: date | None = None
    before: date | None = None
    content_available: bool | None = None
    processing_status: list[Label] = Field(default_factory=list, max_length=10)
    language: list[LanguageCode] = Field(default_factory=list, max_length=20)
    entity_id: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    entity_type: list[Label] = Field(default_factory=list, max_length=20)
    keyword_id: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    story_country: list[CountryCode] = Field(default_factory=list, max_length=50)
    mentioned_country: list[CountryCode] = Field(default_factory=list, max_length=50)
    story_cluster_id: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    sort: Literal["relevance", "newest", "oldest", "most_sources"] = "relevance"
    interval: RequestedInterval = "auto"

    @field_validator("q")
    @classmethod
    def _valid_syntax(cls, value: str) -> str:
        try:
            parse_query(value)
        except SearchSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        return value.strip()

    @model_validator(mode="after")
    def _ordered_range(self) -> Self:
        if self.after and self.before and self.after >= self.before:
            raise ValueError("after must be earlier than before")
        return self


class SavedSearchCreate(BaseModel):
    name: Name
    state: InvestigationState


class SavedSearchUpdate(BaseModel):
    name: Name | None = None
    state: InvestigationState | None = None


class SavedSearchResponse(BaseModel):
    id: uuid.UUID
    name: str
    state_version: int
    state: InvestigationState | None
    problem: str | None
    created_at: datetime
    updated_at: datetime


class SavedSearchPage(BaseModel):
    items: list[SavedSearchResponse]
    next_cursor: str | None
