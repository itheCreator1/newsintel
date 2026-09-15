import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.schemas import GraphEdge, GraphNode, GraphResponse
from app.nlp.models import Entity
from app.search.elasticsearch import ElasticsearchAdapter

MAX_NODES = 50
MAX_EDGES = 150


@dataclass(frozen=True)
class NodeCount:
    entity_id: str
    article_count: int


@dataclass(frozen=True)
class EdgeWeight:
    source: str
    target: str
    weight: int


def _holds_entity(entity_id: str) -> dict[str, Any]:
    """An article-level filter: the article carries a nested entity with this id."""
    return {"nested": {"path": "entities", "query": {"term": {"entities.id": entity_id}}}}


def focus_query(query: dict[str, Any], focus_entity_id: uuid.UUID | None) -> dict[str, Any]:
    """Narrow the search query to articles containing the focus entity."""
    if focus_entity_id is None:
        return query
    holds = _holds_entity(str(focus_entity_id))
    clauses = query.get("bool")
    if clauses is None:
        return {"bool": {"must": [query], "filter": [holds]}}
    return {"bool": {**clauses, "filter": [*clauses.get("filter", []), holds]}}


def nodes_body(
    query: dict[str, Any],
    *,
    entity_types: list[str],
    nodes: int,
    focus_entity_id: uuid.UUID | None,
) -> dict[str, Any]:
    aggs: dict[str, Any] = {
        "entities": {
            "nested": {"path": "entities"},
            "aggs": {
                "filtered": {
                    "filter": (
                        {"terms": {"entities.type": entity_types}}
                        if entity_types
                        else {"match_all": {}}
                    ),
                    "aggs": {
                        "top": {
                            "terms": {"field": "entities.id", "size": min(nodes, MAX_NODES)},
                            "aggs": {"articles": {"reverse_nested": {}}},
                        }
                    },
                }
            },
        }
    }
    if focus_entity_id is not None:
        aggs["focus"] = {
            "nested": {"path": "entities"},
            "aggs": {
                "matched": {
                    "filter": {"term": {"entities.id": str(focus_entity_id)}},
                    "aggs": {"articles": {"reverse_nested": {}}},
                }
            },
        }
    return {"size": 0, "track_total_hits": False, "query": query, "aggs": aggs}


def parse_nodes(
    response: dict[str, Any], *, nodes: int, focus_entity_id: uuid.UUID | None
) -> tuple[list[NodeCount], bool]:
    aggregations = response.get("aggregations", {})
    top = aggregations.get("entities", {}).get("filtered", {}).get("top", {})
    counted = [
        NodeCount(str(bucket["key"]), int(bucket["articles"]["doc_count"]))
        for bucket in top.get("buckets", [])
    ]
    truncated = int(top.get("sum_other_doc_count", 0)) > 0
    limit = min(nodes, MAX_NODES)
    if focus_entity_id is not None:
        focus = str(focus_entity_id)
        if all(item.entity_id != focus for item in counted):
            articles = aggregations.get("focus", {}).get("matched", {}).get("articles", {})
            count = int(articles.get("doc_count", 0))
            if count:
                if len(counted) >= limit:
                    # Evicting a lighter entity to keep the focus is itself a truncation.
                    counted = sorted(counted, key=_ranking)[: limit - 1]
                    truncated = True
                counted.append(NodeCount(focus, count))
    counted.sort(key=_ranking)
    if len(counted) > limit:
        counted, truncated = counted[:limit], True
    return counted, truncated


def _ranking(node: NodeCount) -> tuple[int, str]:
    return (-node.article_count, node.entity_id)


def edges_body(query: dict[str, Any], node_ids: list[str]) -> dict[str, Any]:
    return {
        "size": 0,
        "track_total_hits": False,
        "query": query,
        "aggs": {
            "co_occurrence": {
                "adjacency_matrix": {
                    "filters": {node_id: _holds_entity(node_id) for node_id in node_ids}
                }
            }
        },
    }


def parse_edges(
    response: dict[str, Any], *, node_ids: list[str], min_edge_weight: int
) -> tuple[list[EdgeWeight], bool]:
    order = {node_id: position for position, node_id in enumerate(node_ids)}
    edges: list[EdgeWeight] = []
    buckets = response.get("aggregations", {}).get("co_occurrence", {}).get("buckets", [])
    for bucket in buckets:
        pair = str(bucket["key"]).split("&")
        if len(pair) != 2 or any(node_id not in order for node_id in pair):
            # Single-filter buckets carry no "&"; unknown ids cannot be drawn.
            continue
        weight = int(bucket["doc_count"])
        if weight < min_edge_weight:
            continue
        source, target = sorted(pair, key=lambda node_id: order[node_id])
        edges.append(EdgeWeight(source, target, weight))
    edges.sort(key=lambda edge: (-edge.weight, edge.source, edge.target))
    truncated = len(edges) > MAX_EDGES
    return edges[:MAX_EDGES], truncated


async def _catalogue(db: AsyncSession, node_ids: list[str]) -> dict[str, Entity]:
    identifiers: list[uuid.UUID] = []
    for node_id in node_ids:
        try:
            identifiers.append(uuid.UUID(node_id))
        except ValueError:
            continue
    if not identifiers:
        return {}
    rows = await db.scalars(select(Entity).where(Entity.id.in_(identifiers)))
    return {str(row.id): row for row in rows.all()}


async def entity_graph(
    db: AsyncSession,
    adapter: ElasticsearchAdapter,
    index_name: str,
    *,
    query: dict[str, Any],
    entity_types: list[str],
    nodes: int,
    min_edge_weight: int,
    focus_entity_id: uuid.UUID | None,
) -> GraphResponse:
    response = await adapter.search_index(
        index_name,
        nodes_body(query, entity_types=entity_types, nodes=nodes, focus_entity_id=focus_entity_id),
    )
    counted, truncated = parse_nodes(response, nodes=nodes, focus_entity_id=focus_entity_id)
    catalogue = await _catalogue(db, [item.entity_id for item in counted])
    # An entity the archive no longer knows cannot be labelled, so it cannot be drawn either.
    resolved = [item for item in counted if item.entity_id in catalogue]
    node_ids = [item.entity_id for item in resolved]
    edges: list[EdgeWeight] = []
    if len(node_ids) > 1:
        pairs = await adapter.search_index(index_name, edges_body(query, node_ids))
        edges, edges_truncated = parse_edges(
            pairs, node_ids=node_ids, min_edge_weight=min_edge_weight
        )
        truncated = truncated or edges_truncated
    return GraphResponse(
        nodes=[
            GraphNode(
                id=catalogue[item.entity_id].id,
                text=catalogue[item.entity_id].display_text,
                type=catalogue[item.entity_id].entity_type,
                article_count=item.article_count,
            )
            for item in resolved
        ],
        edges=[
            GraphEdge(
                source=uuid.UUID(edge.source), target=uuid.UUID(edge.target), weight=edge.weight
            )
            for edge in edges
        ],
        truncated=truncated,
    )
