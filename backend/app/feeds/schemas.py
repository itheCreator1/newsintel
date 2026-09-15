import uuid
from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class FeedCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: AnyHttpUrl
    source_country: str | None = Field(default=None, min_length=2, max_length=2)
    expected_language: str | None = Field(default=None, min_length=2, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    poll_interval_minutes: int = Field(default=30, ge=5, le=10080)
    fetching_mode: Literal["rss", "full_text", "full_text_html"] = "rss"


class FeedUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: AnyHttpUrl | None = None
    source_country: str | None = Field(default=None, min_length=2, max_length=2)
    expected_language: str | None = Field(default=None, min_length=2, max_length=16)
    tags: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    fetching_mode: Literal["rss", "full_text", "full_text_html"] | None = None


class FeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    url: str
    source_country: str | None
    expected_language: str | None
    tags: list[str]
    enabled: bool
    poll_interval_minutes: int
    fetching_mode: str
    next_poll_at: datetime
    last_success_at: datetime | None
    created_at: datetime


class CursorPage(BaseModel):
    items: list[FeedResponse]
    next_cursor: str | None


class FetchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    feed_id: uuid.UUID
    status: str
    attempt_count: int
    http_status: int | None
    duration_ms: int | None
    entry_count: int
    invalid_entry_count: int
    new_article_count: int
    error_category: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class FetchPage(BaseModel):
    items: list[FetchResponse]
    next_cursor: str | None


class PollResponse(BaseModel):
    fetch_id: uuid.UUID
    status: str
    reused: bool


class ArticleProvenance(BaseModel):
    feed_id: uuid.UUID
    feed_name: str
    guid: str | None
    title: str
    url: str
    description: str | None
    discovered_at: datetime


class ArticleResponse(BaseModel):
    id: uuid.UUID
    original_url: str
    normalized_url: str
    title: str
    published_at: datetime | None
    first_discovered_at: datetime
    provenance: list[ArticleProvenance]


class ArticleContentResponse(BaseModel):
    text: str
    content_hash: str
    previous_content_hash: str | None
    change_count: int
    extractor_name: str
    extractor_version: str
    extracted_at: datetime
    last_content_change_at: datetime
    html_retained: bool


class ArticleJobSummary(BaseModel):
    id: uuid.UUID
    requested_mode: str
    stage: str
    status: str
    error_category: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class ArticleStoryCluster(BaseModel):
    id: uuid.UUID
    article_count: int
    source_count: int


class RelatedArticle(BaseModel):
    article_id: uuid.UUID
    title: str
    effective_date: datetime
    score: float


class ArticleDetailResponse(ArticleResponse):
    content: ArticleContentResponse | None
    processing: list[ArticleJobSummary]
    clustering_status: str | None = None
    story_cluster: ArticleStoryCluster | None = None
    related: list[RelatedArticle] = Field(default_factory=list)


class ArticlePage(BaseModel):
    items: list[ArticleResponse]
    next_cursor: str | None
