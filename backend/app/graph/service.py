import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clustering.models import StoryCluster
from app.feeds.models import Article, FeedArticle
from app.feeds.service import article_response
from app.graph.schemas import (
    EdgeCluster,
    EdgeEntity,
    EdgeEvidenceResponse,
    GraphEdge,
    GraphNode,
    GraphResponse,
)
from app.nlp.models import Entity
from app.search.elasticsearch import ElasticsearchAdapter

MAX_NODES = 50
MAX_EDGES = 150
MAX_EVIDENCE = 50
EVIDENCE_CLUSTERS = 10
MEANING = (
    "Both entities are mentioned in the same article. "
    "This is co-occurrence, not a stated relationship."
)


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


@dataclass(frozen=True)
class EvidenceHits:
    total: int
    first_at: datetime | None
    last_at: datetime | None
    cluster_count: int
    article_ids: list[uuid.UUID]
    cluster_counts: list[tuple[uuid.UUID, int]]
    next_after: list[Any] | None


def evidence_body(
    query: dict[str, Any],
    source: uuid.UUID,
    target: uuid.UUID,
    *,
    limit: int,
    after: list[Any] | None,
) -> dict[str, Any]:
    """The graph's own query plus both entities, so hits equal the drawn edge weight."""
    body: dict[str, Any] = {
        "size": min(limit, MAX_EVIDENCE) + 1,  # the extra hit reveals whether a page follows
        "track_total_hits": True,
        "query": {
            "bool": {"filter": [query, _holds_entity(str(source)), _holds_entity(str(target))]}
        },
        # ponytail: no PIT, pages can shift if the index is rebuilt mid-scroll; open one if needed
        "sort": [{"effective_date": "desc"}, {"article_id": "asc"}],
        "_source": ["article_id"],
        "aggs": {
            "first": {"min": {"field": "effective_date"}},
            "last": {"max": {"field": "effective_date"}},
            "cluster_count": {"cardinality": {"field": "story_cluster_id"}},
            "clusters": {"terms": {"field": "story_cluster_id", "size": EVIDENCE_CLUSTERS}},
        },
    }
    if after:
        body["search_after"] = after
    return body


def _date(aggregation: dict[str, Any]) -> datetime | None:
    value = aggregation.get("value_as_string")
    return datetime.fromisoformat(value) if value else None


def parse_evidence(response: dict[str, Any], *, limit: int) -> EvidenceHits:
    limit = min(limit, MAX_EVIDENCE)
    hits = response.get("hits", {})
    found = hits.get("hits", [])
    aggregations = response.get("aggregations", {})
    return EvidenceHits(
        total=int(hits.get("total", {}).get("value", 0)),
        first_at=_date(aggregations.get("first", {})),
        last_at=_date(aggregations.get("last", {})),
        cluster_count=int(aggregations.get("cluster_count", {}).get("value", 0)),
        article_ids=[uuid.UUID(hit["_source"]["article_id"]) for hit in found[:limit]],
        cluster_counts=[
            (uuid.UUID(bucket["key"]), int(bucket["doc_count"]))
            for bucket in aggregations.get("clusters", {}).get("buckets", [])
        ],
        next_after=found[limit - 1]["sort"] if len(found) > limit else None,
    )


def encode_after(after: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(after).encode()).decode()


def decode_after(cursor: str) -> list[Any]:
    try:
        after = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc
    if not isinstance(after, list) or len(after) != 2:
        raise ValueError("Invalid cursor")
    return after


async def articles_by_id(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Article]:
    if not ids:
        return {}
    rows = await db.scalars(
        select(Article)
        .where(Article.id.in_(ids))
        .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
    )
    return {row.id: row for row in rows.all()}


async def clusters_by_id(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, StoryCluster]:
    if not ids:
        return {}
    rows = await db.scalars(select(StoryCluster).where(StoryCluster.id.in_(ids)))
    return {row.id: row for row in rows.all()}


def _edge_entity(entity: Entity) -> EdgeEntity:
    return EdgeEntity(id=entity.id, text=entity.display_text, type=entity.entity_type)


def _edge_cluster(
    story: StoryCluster, edge_article_count: int, found: dict[uuid.UUID, Article]
) -> EdgeCluster:
    representative = (
        found.get(story.representative_article_id) if story.representative_article_id else None
    )
    return EdgeCluster(
        id=story.id,
        edge_article_count=edge_article_count,
        article_count=story.article_count,
        source_count=story.source_count,
        representative_article=article_response(representative) if representative else None,
    )


async def edge_evidence(
    db: AsyncSession,
    adapter: ElasticsearchAdapter,
    index_name: str,
    *,
    query: dict[str, Any],
    source: Entity,
    target: Entity,
    limit: int,
    after: list[Any] | None,
) -> EdgeEvidenceResponse:
    response = await adapter.search_index(
        index_name, evidence_body(query, source.id, target.id, limit=limit, after=after)
    )
    hits = parse_evidence(response, limit=limit)
    stories = await clusters_by_id(db, [cluster_id for cluster_id, _ in hits.cluster_counts])
    representatives = [
        story.representative_article_id
        for story in stories.values()
        if story.representative_article_id is not None
    ]
    found = await articles_by_id(db, [*hits.article_ids, *representatives])
    page = [found[article_id] for article_id in hits.article_ids if article_id in found]
    return EdgeEvidenceResponse(
        source=_edge_entity(source),
        target=_edge_entity(target),
        meaning=MEANING,
        article_count=hits.total,
        cluster_count=hits.cluster_count,
        first_at=hits.first_at,
        last_at=hits.last_at,
        articles=[article_response(article) for article in page],
        next_cursor=encode_after(hits.next_after) if hits.next_after else None,
        clusters=[
            _edge_cluster(stories[cluster_id], count, found)
            for cluster_id, count in hits.cluster_counts
            if cluster_id in stories
        ],
        missing_from_archive=len(hits.article_ids) - len(page),
    )
