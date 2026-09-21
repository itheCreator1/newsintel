import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

State = Literal["ok", "degraded", "down", "unknown"]
FeedState = Literal["disabled", "failing", "overdue", "awaiting", "ok"]
Area = Literal["feed", "article", "search", "nlp", "cluster", "monitor", "event"]


class Probe(BaseModel):
    name: str
    state: State
    latency_ms: int | None = None
    detail: str | None = None
    checked_at: datetime


class QueueDepth(BaseModel):
    queue: str
    ready: int
    delayed: int
    dead: int


class HealthResponse(BaseModel):
    generated_at: datetime
    probes: list[Probe]
    queues: list[QueueDepth]


class JobPipeline(BaseModel):
    key: str
    label: str
    definition: str
    window_basis: str
    queued: int
    running: int
    retrying: int
    failed: int
    lease_expired: int
    oldest_wait_seconds: float | None
    completed_in_window: int
    failed_in_window: int


class RunError(BaseModel):
    at: datetime
    category: str | None
    message: str | None


class EventPipeline(BaseModel):
    key: str = "events"
    label: str
    definition: str
    algorithm_version: str
    dirty_clusters: int
    last_run_at: datetime | None
    last_success_at: datetime | None
    runs_in_window: int
    failed_runs_in_window: int
    failed_clusters_in_window: int
    last_error: RunError | None


class CategoryCount(BaseModel):
    category: str
    count: int


class MonitorPipeline(BaseModel):
    key: str = "monitors"
    label: str
    definition: str
    total: int
    enabled: int
    due: int
    oldest_overdue_seconds: float | None
    in_error: int
    by_error_category: list[CategoryCount]


class PipelinesResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    window_start: datetime
    jobs: list[JobPipeline]
    events: EventPipeline
    monitors: MonitorPipeline


class FeedHealth(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    poll_interval_minutes: int
    state: FeedState
    last_fetch_status: str | None
    last_fetch_at: datetime | None
    last_success_at: datetime | None
    next_poll_at: datetime
    overdue_seconds: int | None
    failure_streak: int
    streak_capped: bool
    failures_by_category: dict[str, int]


class FeedTotals(BaseModel):
    fetches: int
    entries: int
    invalid: int
    new: int
    duplicates: int


class FeedsResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    window_start: datetime
    truncated: bool
    items: list[FeedHealth]
    totals: FeedTotals


class TableSize(BaseModel):
    name: str
    total_bytes: int
    approximate_rows: int


class ElasticsearchStorage(BaseModel):
    index: str
    documents: int
    store_bytes: int


class StorageResponse(BaseModel):
    generated_at: datetime
    database_bytes: int
    tables: list[TableSize]
    retained_html_objects: int
    article_files_measured: bool
    article_files_note: str
    elasticsearch: ElasticsearchStorage | None
    elasticsearch_error: str | None


class FailureItem(BaseModel):
    id: uuid.UUID
    ref_id: uuid.UUID | None
    status: str | None
    at: datetime
    error_category: str | None
    message: str | None


class FailuresResponse(BaseModel):
    generated_at: datetime
    area: Area
    window_hours: int
    window_start: datetime
    by_category: list[CategoryCount]
    recent: list[FailureItem]
