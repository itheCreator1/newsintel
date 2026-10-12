import uuid
from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.marks.schemas import ArticleMarkResponse

MAX_TAG_LENGTH = 40
FetchingMode = Literal["rss", "full_text", "full_text_html"]


def _clean_tags(tags: list[str] | None) -> list[str] | None:
    """Trimmed, inner spaces collapsed, empties dropped, and one of each ignoring case."""
    if tags is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = " ".join(raw.split())
        if not tag or tag.casefold() in seen:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"A tag is at most {MAX_TAG_LENGTH} characters")
        seen.add(tag.casefold())
        cleaned.append(tag)
    return cleaned


class _FeedCodes(BaseModel):
    @field_validator("source_country", check_fields=False)
    @classmethod
    def _upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("expected_language", check_fields=False)
    @classmethod
    def _lower_language(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @field_validator("tags", check_fields=False)
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        return _clean_tags(value)


class FeedCreate(_FeedCodes):
    name: str = Field(min_length=1, max_length=200)
    url: AnyHttpUrl
    source_country: str | None = Field(default=None, min_length=2, max_length=2)
    expected_language: str | None = Field(default=None, min_length=2, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    poll_interval_minutes: int = Field(default=60, ge=5, le=10080)
    fetching_mode: FetchingMode = "rss"


class FeedUpdate(_FeedCodes):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: AnyHttpUrl | None = None
    source_country: str | None = Field(default=None, min_length=2, max_length=2)
    expected_language: str | None = Field(default=None, min_length=2, max_length=16)
    tags: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    fetching_mode: FetchingMode | None = None


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
    fetching_mode: FetchingMode
    next_poll_at: datetime
    last_success_at: datetime | None
    created_at: datetime
    retired_at: datetime | None = None
    # The newest finished fetch, filled in by the list.
    last_fetch_status: str | None = None
    last_fetch_error: str | None = None
    last_fetch_at: datetime | None = None


class FeedTagCount(BaseModel):
    tag: str
    count: int


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
    # The reader's own marks; absent only where a detail is built outside the article route.
    mark: ArticleMarkResponse | None = None


class ArticleListItem(ArticleResponse):
    read: bool
    starred: bool


class ArticlePage(BaseModel):
    items: list[ArticleListItem]
    next_cursor: str | None


class OpmlPreviewRequest(BaseModel):
    # The file's text, read in the browser; 5 MB holds thousands of subscriptions.
    opml: str = Field(min_length=1, max_length=5_000_000)


class OpmlPreviewItem(BaseModel):
    name: str
    url: str
    tags: list[str]
    source_country: str | None
    expected_language: str | None
    # A source that is not retired already collects this address.
    existing_feed_id: uuid.UUID | None


class OpmlPreview(BaseModel):
    items: list[OpmlPreviewItem]
    invalid: int


class FeedImport(BaseModel):
    feeds: list[FeedCreate] = Field(min_length=1, max_length=2000)


class FeedImportResult(BaseModel):
    created: int
    skipped: int


class FeedTestRequest(BaseModel):
    url: AnyHttpUrl


class FeedCandidate(BaseModel):
    url: str
    title: str | None


class FeedTestResponse(BaseModel):
    url: str
    title: str | None
    entry_count: int
    language: str | None
    latest_entry_at: datetime | None
    discovered_from: str | None
    candidates: list[FeedCandidate]
    existing_feed_id: uuid.UUID | None
