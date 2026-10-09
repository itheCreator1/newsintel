import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# In page order: the order of the cards and of the activity list's process filter.
ProcessKey = Literal[
    "feeds", "articles", "nlp", "clustering", "search", "monitors",
    "reprocessing", "authority", "rebuild", "source_refresh",
    "events", "retention", "wikidata_refresh", "wikidata_candidates",
]  # fmt: skip
ProcessGroup = Literal["per_item", "bulk", "scheduled"]
# ok: up to date; working: has work in hand; retrying: items wait to be tried again; stalled: a
# worker took an item and stopped renewing it, or Wikidata waits; failing: something failed in the
# window; idle: has never run or has nothing to do; off: switched off by configuration.
ProcessState = Literal["ok", "working", "retrying", "stalled", "failing", "idle", "off"]
ActivityStatus = Literal["queued", "running", "retrying", "failed", "succeeded", "stopped"]
ActivityFilter = Literal["attention", "failed", "running", "queued", "done", "all"]
LinkKind = Literal["article", "feed", "entity", "monitor"]


class Progress(BaseModel):
    done: int
    total: int | None


class ProcessCard(BaseModel):
    key: ProcessKey
    group: ProcessGroup
    label: str
    description: str
    state: ProcessState
    queued: int | None = None
    running: int | None = None
    retrying: int | None = None
    failed: int | None = None
    lease_expired: int | None = None
    oldest_wait_seconds: float | None = None
    done_in_window: int | None = None
    failed_in_window: int | None = None
    last_run_at: datetime | None = None
    active_run_id: uuid.UUID | None = None
    progress: Progress | None = None
    detail: str | None = None


class ProcessesResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    window_start: datetime
    processes: list[ProcessCard]


class ActivityAttempt(BaseModel):
    number: int
    stage: str
    status: str
    error_category: str | None
    error_message: str | None
    started_at: datetime


class ActivityItem(BaseModel):
    process: ProcessKey
    id: uuid.UUID
    status: ActivityStatus
    title: str
    detail: str | None
    link_kind: LinkKind | None
    link_id: uuid.UUID | None
    at: datetime
    error_category: str | None
    error_message: str | None
    attempt_count: int | None
    attempts: list[ActivityAttempt] = []


class ActivityCounts(BaseModel):
    attention: int
    failed: int
    running: int
    queued: int


class ActivityPage(BaseModel):
    generated_at: datetime
    window_hours: int
    window_start: datetime
    items: list[ActivityItem]
    next_cursor: str | None
    counts: ActivityCounts
