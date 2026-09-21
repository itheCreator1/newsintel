import uuid
from datetime import datetime

from pydantic import BaseModel

from app.feeds.schemas import ArticleResponse


class GraphNode(BaseModel):
    id: uuid.UUID
    text: str
    type: str
    article_count: int


class GraphEdge(BaseModel):
    source: uuid.UUID
    target: uuid.UUID
    weight: int


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


class EdgeEntity(BaseModel):
    id: uuid.UUID
    text: str
    type: str


class EdgeCluster(BaseModel):
    id: uuid.UUID
    edge_article_count: int
    article_count: int
    source_count: int
    representative_article: ArticleResponse | None


class EdgeEvidenceResponse(BaseModel):
    source: EdgeEntity
    target: EdgeEntity
    meaning: str
    article_count: int
    cluster_count: int
    # Elasticsearch cardinality: near-exact below 3000 distinct stories, never guaranteed exact.
    cluster_count_estimated: bool
    first_at: datetime | None
    last_at: datetime | None
    articles: list[ArticleResponse]
    next_cursor: str | None
    clusters: list[EdgeCluster]
    missing_from_archive: int
