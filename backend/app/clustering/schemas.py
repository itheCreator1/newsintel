import uuid
from datetime import datetime

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
