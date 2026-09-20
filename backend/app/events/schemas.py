import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.entities.schemas import EntityClusterResponse
from app.feeds.schemas import ArticleResponse


class EventEntityResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    entity_type: str
    article_count: int


class EventSummary(BaseModel):
    """`cluster_count`, `article_count` and `source_count` are aggregated when asked, never stored.

    An event has no title of its own: `headline` is the title of the representative article of its
    largest member cluster (ties: earliest first publication, then cluster id).
    """

    id: uuid.UUID
    algorithm_version: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    primary_country: str | None
    cluster_count: int
    article_count: int
    source_count: int
    headline: str | None
    headline_article_id: uuid.UUID | None
    entities: list[EventEntityResponse]


class EventDetail(EventSummary):
    created_at: datetime
    updated_at: datetime


class EventPage(BaseModel):
    items: list[EventSummary]
    next_cursor: str | None


class EventClusterResponse(EntityClusterResponse):
    """A member cluster with the stored reason it joined the event."""

    score: float
    signals: dict[str, Any] = Field(default_factory=dict)
    joined_at: datetime


class EventClusterPage(BaseModel):
    items: list[EventClusterResponse]
    next_cursor: str | None


class EventArticleResponse(ArticleResponse):
    cluster_id: uuid.UUID


class EventArticlePage(BaseModel):
    items: list[EventArticleResponse]
    next_cursor: str | None


class TimelineEvidence(BaseModel):
    article_id: uuid.UUID
    title: str


class EventTimelineDay(BaseModel):
    """One UTC day of the event's articles; `evidence` is the day's earliest articles (up to 3)."""

    date: date
    article_count: int
    source_count: int
    clusters_started: int
    evidence: list[TimelineEvidence]


class EventTimelinePage(BaseModel):
    items: list[EventTimelineDay]
    next_cursor: date | None
