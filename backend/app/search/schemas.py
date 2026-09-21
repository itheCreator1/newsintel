import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HighlightSegment(BaseModel):
    text: str
    marked: bool = False


class SearchResultSource(BaseModel):
    id: uuid.UUID
    name: str
    country: str | None


class StoryClusterRef(BaseModel):
    id: uuid.UUID
    source_count: int


class SearchResult(BaseModel):
    article_id: uuid.UUID
    title: str
    effective_date: datetime
    distinct_source_count: int
    sources: list[str]
    source_refs: list[SearchResultSource]
    story_country: str | None = None
    story_cluster: StoryClusterRef | None = None
    summary: str | None
    highlights: list[HighlightSegment]


class SearchPage(BaseModel):
    items: list[SearchResult]
    next_cursor: str | None


class IndexStatus(BaseModel):
    queued: int = 0
    running: int = 0
    retrying: int = 0
    failed: int = 0
    active_rebuild: dict[str, object] | None = None


class IndexFailure(BaseModel):
    id: uuid.UUID
    article_id: uuid.UUID
    index_name: str
    error_category: str | None
    error_message: str | None
    attempt_count: int
    updated_at: datetime


class IndexFailurePage(BaseModel):
    items: list[IndexFailure]
    next_cursor: str | None


class RetryIndexResponse(BaseModel):
    status: str


class SearchSource(BaseModel):
    id: uuid.UUID
    name: str
    source_country: str | None
    retired: bool


class SearchSourcePage(BaseModel):
    items: list[SearchSource]
    next_cursor: str | None


class TimelineBucket(BaseModel):
    start: datetime
    count: int


class SearchTimeline(BaseModel):
    interval: Literal["hour", "day", "week", "month", "year"]
    total: int
    buckets: list[TimelineBucket]


class FacetBucket(BaseModel):
    value: str
    label: str | None
    count: int


class FacetGroup(BaseModel):
    buckets: list[FacetBucket]
    truncated: bool


class SearchFacets(BaseModel):
    total: int
    sources: FacetGroup
    source_countries: FacetGroup
    story_countries: FacetGroup
    mentioned_countries: FacetGroup
    languages: FacetGroup
    entities: FacetGroup
    entity_types: FacetGroup
    keywords: FacetGroup
    story_clusters: FacetGroup
