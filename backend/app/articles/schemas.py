import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcessRequest(BaseModel):
    mode: Literal["full_text", "full_text_html"] = "full_text"


class ProcessResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    reused: bool


class ContentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    text: str
    content_hash: str
    previous_content_hash: str | None
    change_count: int
    extractor_name: str
    extractor_version: str
    extracted_at: datetime
    last_content_change_at: datetime
    html_retained: bool


class AttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    stage: str
    attempt_number: int
    status: str
    http_status: int | None
    error_category: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    article_id: uuid.UUID
    article_title: str
    requested_mode: str
    stage: str
    status: str
    error_category: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    next_attempt_at: datetime
    attempts: list[AttemptResponse] = Field(default_factory=list)


class JobPage(BaseModel):
    items: list[JobResponse]
    next_cursor: str | None


class BacklogResponse(BaseModel):
    queued: int
    running: int
    retrying: int
    failed: int
