import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ClusteringStatusResponse(BaseModel):
    queued: int = 0
    running: int = 0
    retrying: int = 0
    failed: int = 0
    algorithm_version: str
    clusters: int = 0
    clustered_articles: int = 0


class ClusteringFailureResponse(BaseModel):
    id: uuid.UUID
    article_id: uuid.UUID
    algorithm_version: str
    attempt_count: int
    error_category: str | None
    error_message: str | None
    created_at: datetime


class ClusteringFailurePage(BaseModel):
    items: list[ClusteringFailureResponse]
    next_cursor: str | None


class ClusteringMutationResponse(BaseModel):
    status: str
    jobs_created: int


class ClusterMemberFeedRef(BaseModel):
    id: uuid.UUID
    name: str


class ClusterMemberResponse(BaseModel):
    article_id: uuid.UUID
    title: str
    effective_date: datetime
    feeds: list[ClusterMemberFeedRef]
    score: float


class ClusterMemberPage(BaseModel):
    items: list[ClusterMemberResponse]
    next_cursor: str | None


class StoryClusterDetailResponse(BaseModel):
    id: uuid.UUID
    algorithm_version: str
    article_count: int
    source_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    representative_article_id: uuid.UUID | None
    members: ClusterMemberPage


class StoryEntity(BaseModel):
    id: uuid.UUID
    display_name: str
    entity_type: str
    article_count: int


class StoryEventRef(BaseModel):
    id: uuid.UUID
    headline: str | None


class StoryTimelineBucket(BaseModel):
    start: datetime
    count: int


class StoryTimeline(BaseModel):
    interval: Literal["hour", "day"]
    buckets: list[StoryTimelineBucket]


class StorySource(BaseModel):
    id: uuid.UUID
    name: str
    first_reported_at: datetime
    minutes_behind: float


class StorySummary(BaseModel):
    """The story page's header. `title` is the representative article's, else the earliest's.

    `sources` lists each source once, at its first article, earliest first: the first one
    reported the story first.
    """

    id: uuid.UUID
    title: str | None
    entities: list[StoryEntity]
    event: StoryEventRef | None
    timeline: StoryTimeline
    sources: list[StorySource]
