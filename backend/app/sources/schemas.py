import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.entities.schemas import (
    EntityClusterResponse,
    RelatedCountryResponse,
    RelatedEntityResponse,
)
from app.feeds.schemas import ArticleResponse, FeedResponse, FetchResponse


class SourceHealth(BaseModel):
    """Derived from the feed row and its fetch history; there is no stored health score."""

    last_attempt_at: datetime | None
    last_attempt_status: str | None
    last_failure: FetchResponse | None
    consecutive_failures: int


class SourcePublishing(BaseModel):
    """`with_published_at` is out of `articles`: the rest use the discovery time as their date."""

    articles: int
    with_published_at: int


class SourceExtraction(BaseModel):
    """Every count is out of `articles`. `not_extracted` is what is left: no content, no failed or
    active job (an RSS-only feed never requests extraction)."""

    articles: int
    extracted: int
    failed: int
    in_progress: int
    not_extracted: int


class SourceFetches(BaseModel):
    total: int
    success: int
    failed: int
    new_articles: int
    mean_duration_ms: float | None
    failures_by_category: dict[str, int]


class SourceTimelineDay(BaseModel):
    date: date
    article_count: int


class SourceDetail(FeedResponse):
    retired_at: datetime | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    health: SourceHealth
    window_days: int
    publishing: SourcePublishing
    extraction: SourceExtraction
    fetches: SourceFetches
    timeline: list[SourceTimelineDay]


class SourceLanguage(BaseModel):
    language: str
    article_count: int


class SourceCoverage(BaseModel):
    window_days: int
    articles: int
    entities: list[RelatedEntityResponse]
    countries: list[RelatedCountryResponse]
    languages: list[SourceLanguage]


class SourceTiming(BaseModel):
    """Over stories in the window that this source and at least one other source published.

    `first` counts stories where this source's article is the earliest (ties: lowest article id; an
    article that several feeds carry counts for each of them). The two minute values describe the
    remaining stories: how far behind the earliest article this source's own article was.
    """

    window_days: int
    stories: int
    first: int
    median_minutes_behind: float | None
    p90_minutes_behind: float | None


class SourceArticlePage(BaseModel):
    items: list[ArticleResponse]
    next_cursor: str | None


class SourceClusterResponse(EntityClusterResponse):
    """`first`/`minutes_behind` are null for a story only this source published."""

    source_article_id: uuid.UUID
    first: bool | None
    minutes_behind: float | None


class SourceClusterPage(BaseModel):
    items: list[SourceClusterResponse]
    next_cursor: str | None
