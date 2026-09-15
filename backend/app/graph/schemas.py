import uuid

from pydantic import BaseModel


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
