import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OccurrenceResponse(BaseModel):
    section: str
    reference_id: str | None
    start: int
    end: int
    input_start: int
    input_end: int


class ProcessorOutcomeResponse(BaseModel):
    processor: str
    status: str
    requested_generation: int
    completed_generation: int
    processor_version: str
    configuration_fingerprint: str
    input_fingerprint: str
    algorithm_version: str | None = None
    model_version: str | None = None
    detail: str | None = None
    completed_at: datetime | None = None


class LanguageAnnotationResponse(BaseModel):
    language: str
    confidence: float
    margin: float
    fresh: bool


class KeywordAnnotationResponse(BaseModel):
    id: uuid.UUID
    text: str
    normalized_text: str
    kind: str
    occurrence_count: int
    raw_score: float
    relevance: float
    occurrences: list[OccurrenceResponse]
    fresh: bool


class EntityAnnotationResponse(BaseModel):
    id: uuid.UUID
    text: str
    normalized_text: str
    entity_type: str
    original_label: str | None
    occurrence_count: int
    relevance: float
    occurrences: list[OccurrenceResponse]
    fresh: bool


class CountryAnnotationResponse(BaseModel):
    country_code: str
    role: str
    inferred: bool
    rule_version: str
    occurrence_count: int
    occurrences: list[OccurrenceResponse]
    fresh: bool


class CapabilityResponse(BaseModel):
    name: str
    state: str
    version: str | None = None
    detail: str | None = None


class ArticleAnnotationsResponse(BaseModel):
    article_id: uuid.UUID
    source_countries: list[str]
    language: LanguageAnnotationResponse | None
    keywords: list[KeywordAnnotationResponse]
    entities: list[EntityAnnotationResponse]
    countries: list[CountryAnnotationResponse]
    processors: list[ProcessorOutcomeResponse]
    capabilities: list[CapabilityResponse]


class NlpStatusResponse(BaseModel):
    queued: int = 0
    running: int = 0
    retrying: int = 0
    failed: int = 0
    capabilities: list[CapabilityResponse]
    reprocessing: list[dict[str, object]]


class NlpFailureResponse(BaseModel):
    id: uuid.UUID
    article_id: uuid.UUID
    processor: str
    attempt_count: int
    error_category: str | None
    error_message: str | None
    created_at: datetime


class NlpFailurePage(BaseModel):
    items: list[NlpFailureResponse]
    next_cursor: str | None


class ReprocessRequest(BaseModel):
    processors: list[str] = Field(
        default_factory=lambda: ["language", "keywords", "entities", "countries"],
        min_length=1,
        max_length=4,
    )


class NlpMutationResponse(BaseModel):
    status: str
    jobs_created: int


class StopWordsResponse(BaseModel):
    language: str
    revision: int
    words: list[str]
    configuration_fingerprint: str


class StopWordsUpdate(BaseModel):
    current_revision: int = Field(ge=1)
    words: list[str] = Field(max_length=5000)


class AnnotationLookupItem(BaseModel):
    id: uuid.UUID
    text: str
    normalized_text: str
    kind: str


class AnnotationLookupPage(BaseModel):
    items: list[AnnotationLookupItem]
    next_cursor: str | None
