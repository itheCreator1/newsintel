import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.graph.routes import entity_graph
from app.graph.schemas import GraphResponse
from app.graph.service import (
    MAX_EDGES,
    MAX_NODES,
    edges_body,
    focus_query,
    nodes_body,
    parse_edges,
    parse_nodes,
)
from app.main import create_app
from app.nlp.models import Entity
from app.search.criteria import SearchCriteria, build_query
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.query import parse_query


def _criteria(q: str = "", **overrides: object) -> SearchCriteria:
    values: dict[str, object] = {
        "parsed": parse_query(q),
        "q": q,
        "sources": [],
        "countries": [],
        "start": None,
        "end": None,
        "content_available": None,
        "processing": [],
        "languages": [],
        "entity_ids": [],
        "entity_types": [],
        "keyword_ids": [],
        "story_countries": [],
        "mentioned_countries": [],
        "story_cluster_ids": [],
    }
    values.update(overrides)
    return SearchCriteria(**values)  # type: ignore[arg-type]


def _bucket(key: str, nested: int, articles: int) -> dict[str, Any]:
    return {"key": key, "doc_count": nested, "articles": {"doc_count": articles}}


def _nodes_response(
    buckets: list[dict[str, Any]], other: int = 0, focus: int | None = None
) -> dict[str, Any]:
    aggregations: dict[str, Any] = {
        "entities": {
            "filtered": {"top": {"sum_other_doc_count": other, "buckets": buckets}},
        }
    }
    if focus is not None:
        aggregations["focus"] = {"matched": {"articles": {"doc_count": focus}}}
    return {"aggregations": aggregations}


def _edges_response(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    return {"aggregations": {"co_occurrence": {"buckets": buckets}}}


# --- query bodies -------------------------------------------------------------------


def test_nodes_body_counts_articles_per_entity_within_the_requested_types() -> None:
    body = nodes_body(
        build_query(_criteria(), schema_version=2),
        entity_types=["ORG", "PERSON"],
        nodes=12,
        focus_entity_id=None,
    )

    assert body["size"] == 0
    assert body["track_total_hits"] is False
    entities = body["aggs"]["entities"]
    assert entities["nested"] == {"path": "entities"}
    filtered = entities["aggs"]["filtered"]
    assert filtered["filter"] == {"terms": {"entities.type": ["ORG", "PERSON"]}}
    assert filtered["aggs"]["top"] == {
        "terms": {"field": "entities.id", "size": 12},
        "aggs": {"articles": {"reverse_nested": {}}},
    }
    assert "focus" not in body["aggs"]


def test_nodes_body_keeps_every_entity_type_when_none_are_requested() -> None:
    body = nodes_body(
        build_query(_criteria(), schema_version=2), entity_types=[], nodes=30, focus_entity_id=None
    )

    assert body["aggs"]["entities"]["aggs"]["filtered"]["filter"] == {"match_all": {}}


def test_focus_entity_narrows_the_base_query_instead_of_only_the_terms_aggregation() -> None:
    focus = uuid.uuid4()
    base = build_query(_criteria(languages=["en"]), schema_version=2)

    narrowed = focus_query(base, focus)

    assert narrowed["bool"]["must"] == base["bool"]["must"]
    assert narrowed["bool"]["filter"] == [
        *base["bool"]["filter"],
        {"nested": {"path": "entities", "query": {"term": {"entities.id": str(focus)}}}},
    ]
    assert base["bool"]["filter"] == [{"terms": {"detected_language": ["en"]}}]


def test_nodes_body_counts_the_focus_entity_alongside_the_top_terms() -> None:
    focus = uuid.uuid4()

    body = nodes_body(
        build_query(_criteria(), schema_version=2),
        entity_types=["PERSON"],
        nodes=5,
        focus_entity_id=focus,
    )

    assert body["aggs"]["focus"] == {
        "nested": {"path": "entities"},
        "aggs": {
            "matched": {
                "filter": {"term": {"entities.id": str(focus)}},
                "aggs": {"articles": {"reverse_nested": {}}},
            }
        },
    }


def test_edges_body_declares_one_nested_filter_per_selected_node() -> None:
    query = focus_query(build_query(_criteria(), schema_version=2), None)

    body = edges_body(query, ["n1", "n2"])

    assert body["size"] == 0
    assert body["track_total_hits"] is False
    assert body["query"] == query
    filters = body["aggs"]["co_occurrence"]["adjacency_matrix"]["filters"]
    assert filters == {
        "n1": {"nested": {"path": "entities", "query": {"term": {"entities.id": "n1"}}}},
        "n2": {"nested": {"path": "entities", "query": {"term": {"entities.id": "n2"}}}},
    }


# --- aggregation parsing and bounding ----------------------------------------------


def test_parse_nodes_ranks_by_the_reverse_nested_article_count() -> None:
    response = _nodes_response([_bucket("a", nested=10, articles=2), _bucket("b", 3, articles=5)])

    counted, truncated = parse_nodes(response, nodes=10, focus_entity_id=None)

    assert [(item.entity_id, item.article_count) for item in counted] == [("b", 5), ("a", 2)]
    assert truncated is False


def test_parse_nodes_reports_truncation_when_more_candidates_existed() -> None:
    response = _nodes_response([_bucket("a", 4, 4)], other=7)

    _, truncated = parse_nodes(response, nodes=10, focus_entity_id=None)

    assert truncated is True


def test_parse_nodes_never_returns_more_nodes_than_requested() -> None:
    buckets = [_bucket(f"n{index}", 1, 10 - index) for index in range(8)]

    counted, truncated = parse_nodes(_nodes_response(buckets), nodes=3, focus_entity_id=None)

    assert [item.entity_id for item in counted] == ["n0", "n1", "n2"]
    assert truncated is True


def test_parse_nodes_splices_a_focus_entity_the_type_filter_excluded() -> None:
    focus = uuid.uuid4()
    buckets = [_bucket("a", 1, 9), _bucket("b", 1, 4), _bucket("c", 1, 2)]

    counted, truncated = parse_nodes(
        _nodes_response(buckets, focus=12), nodes=3, focus_entity_id=focus
    )

    assert [(item.entity_id, item.article_count) for item in counted] == [
        (str(focus), 12),
        ("a", 9),
        ("b", 4),
    ]
    assert truncated is True


def test_parse_nodes_leaves_a_focus_entity_the_terms_aggregation_already_returned() -> None:
    focus = uuid.uuid4()
    buckets = [_bucket(str(focus), 1, 12), _bucket("a", 1, 9)]

    counted, truncated = parse_nodes(
        _nodes_response(buckets, focus=12), nodes=3, focus_entity_id=focus
    )

    assert [item.entity_id for item in counted] == [str(focus), "a"]
    assert truncated is False


def test_parse_edges_keeps_only_pairs_at_or_above_the_minimum_weight() -> None:
    response = _edges_response(
        [
            {"key": "a", "doc_count": 9},
            {"key": "a&b", "doc_count": 4},
            {"key": "a&c", "doc_count": 1},
            {"key": "b&c", "doc_count": 2},
            {"key": "b&zz", "doc_count": 9},
        ]
    )

    edges, truncated = parse_edges(response, node_ids=["a", "b", "c"], min_edge_weight=2)

    assert [(edge.source, edge.target, edge.weight) for edge in edges] == [
        ("a", "b", 4),
        ("b", "c", 2),
    ]
    assert truncated is False


def test_parse_edges_orients_every_pair_by_the_node_order() -> None:
    response = _edges_response([{"key": "b&a", "doc_count": 3}])

    edges, _ = parse_edges(response, node_ids=["a", "b"], min_edge_weight=2)

    assert [(edge.source, edge.target) for edge in edges] == [("a", "b")]


def test_parse_edges_keeps_the_heaviest_pairs_within_the_edge_cap() -> None:
    node_ids = [f"n{index}" for index in range(40)]
    buckets = [
        {"key": f"{left}&{right}", "doc_count": weight}
        for weight, (left, right) in enumerate(
            (left, right)
            for index, left in enumerate(node_ids)
            for right in node_ids[index + 1 :]
        )
    ]
    assert len(buckets) > MAX_EDGES

    edges, truncated = parse_edges(_edges_response(buckets), node_ids=node_ids, min_edge_weight=2)

    assert len(edges) == MAX_EDGES
    assert truncated is True
    assert [edge.weight for edge in edges] == sorted(
        (bucket["doc_count"] for bucket in buckets), reverse=True
    )[:MAX_EDGES]


# --- route --------------------------------------------------------------------------


class _Target:
    index_name = "articles-v2-test"

    def __init__(self, schema_version: int) -> None:
        self.schema_version = schema_version


class _Scalars:
    def __init__(self, rows: list[Entity]) -> None:
        self.rows = rows

    def all(self) -> list[Entity]:
        return self.rows


class _Database:
    def __init__(self, schema_version: int = 2, entities: list[Entity] | None = None) -> None:
        self.target = _Target(schema_version)
        self.entities = entities or []

    async def scalar(self, _query: object) -> _Target:
        return self.target

    async def scalars(self, _query: object) -> _Scalars:
        return _Scalars(self.entities)


class _Adapter:
    responses: list[dict[str, Any]] = []
    bodies: list[tuple[str, dict[str, Any]]] = []
    unavailable = False

    def __init__(self, _url: str) -> None:
        pass

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        if _Adapter.unavailable:
            raise ElasticsearchUnavailable("connection refused")
        _Adapter.bodies.append((index_name, body))
        return _Adapter.responses.pop(0)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> type[_Adapter]:
    _Adapter.responses, _Adapter.bodies, _Adapter.unavailable = [], [], False
    monkeypatch.setattr("app.graph.routes.ElasticsearchAdapter", _Adapter)
    return _Adapter


def _entity(entity_id: uuid.UUID, text: str, entity_type: str = "ORG") -> Entity:
    return Entity(
        id=entity_id,
        language="en",
        entity_type=entity_type,
        normalized_text=text.casefold(),
        display_text=text,
    )


async def _graph(
    database: _Database,
    criteria: SearchCriteria | None = None,
    focus_entity_id: uuid.UUID | None = None,
    nodes: int = 30,
    min_edge_weight: int = 2,
) -> GraphResponse:
    return await entity_graph(
        database,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Settings(),
        criteria or _criteria(),
        focus_entity_id,
        nodes,
        min_edge_weight,
    )


@pytest.mark.asyncio
async def test_graph_labels_nodes_from_the_entity_catalogue(adapter: type[_Adapter]) -> None:
    left, right = uuid.uuid4(), uuid.uuid4()
    adapter.responses = [
        _nodes_response([_bucket(str(left), 6, 6), _bucket(str(right), 4, 4)]),
        _edges_response(
            [
                {"key": str(left), "doc_count": 6},
                {"key": f"{left}&{right}", "doc_count": 3},
            ]
        ),
    ]
    database = _Database(entities=[_entity(left, "Harbour Authority"), _entity(right, "Ada Reyes")])
    criteria = _criteria(languages=["en"])

    graph = await _graph(database, criteria)

    assert [(node.id, node.text, node.article_count) for node in graph.nodes] == [
        (left, "Harbour Authority", 6),
        (right, "Ada Reyes", 4),
    ]
    assert [(edge.source, edge.target, edge.weight) for edge in graph.edges] == [(left, right, 3)]
    assert graph.truncated is False
    (nodes_index, nodes_request), (edges_index, edges_request) = adapter.bodies
    assert nodes_index == edges_index == "articles-v2-test"
    assert nodes_request["query"] == edges_request["query"] == build_query(criteria, 2)


@pytest.mark.asyncio
async def test_graph_narrows_both_requests_to_articles_holding_the_focus_entity(
    adapter: type[_Adapter],
) -> None:
    focus, other = uuid.uuid4(), uuid.uuid4()
    adapter.responses = [
        _nodes_response([_bucket(str(focus), 5, 5), _bucket(str(other), 3, 3)], focus=5),
        _edges_response([{"key": f"{focus}&{other}", "doc_count": 3}]),
    ]
    database = _Database(entities=[_entity(focus, "Port Trust"), _entity(other, "Ada Reyes")])

    graph = await _graph(database, focus_entity_id=focus)

    assert [node.id for node in graph.nodes] == [focus, other]
    expected = focus_query(build_query(_criteria(), 2), focus)
    assert [body["query"] for _, body in adapter.bodies] == [expected, expected]


@pytest.mark.asyncio
async def test_graph_drops_entities_missing_from_the_catalogue_and_their_edges(
    adapter: type[_Adapter],
) -> None:
    known, stale = uuid.uuid4(), uuid.uuid4()
    adapter.responses = [
        _nodes_response([_bucket(str(known), 6, 6), _bucket(str(stale), 4, 4)]),
    ]

    graph = await _graph(_Database(entities=[_entity(known, "Harbour Authority")]))

    assert [node.id for node in graph.nodes] == [known]
    assert graph.edges == []
    assert len(adapter.bodies) == 1


@pytest.mark.asyncio
async def test_graph_skips_the_adjacency_request_without_a_pair_to_compare(
    adapter: type[_Adapter],
) -> None:
    only = uuid.uuid4()
    adapter.responses = [_nodes_response([_bucket(str(only), 6, 6)])]

    graph = await _graph(_Database(entities=[_entity(only, "Harbour Authority")]))

    assert [node.id for node in graph.nodes] == [only]
    assert graph.edges == []
    assert len(adapter.bodies) == 1


@pytest.mark.asyncio
async def test_graph_caps_the_payload_when_elasticsearch_offers_more(
    adapter: type[_Adapter],
) -> None:
    ids = [uuid.uuid4() for _ in range(40)]
    adapter.responses = [
        _nodes_response([_bucket(str(value), 1, 40 - index) for index, value in enumerate(ids)]),
        _edges_response(
            [
                {"key": f"{left}&{right}", "doc_count": 2 + index}
                for index, (left, right) in enumerate(
                    (left, right)
                    for position, left in enumerate(ids)
                    for right in ids[position + 1 :]
                )
            ]
        ),
    ]
    database = _Database(
        entities=[_entity(value, f"Entity {index}") for index, value in enumerate(ids)]
    )

    graph = await _graph(database, nodes=25)

    assert len(graph.nodes) == 25
    assert len(graph.edges) == MAX_EDGES
    assert graph.truncated is True
    assert len(adapter.bodies[1][1]["aggs"]["co_occurrence"]["adjacency_matrix"]["filters"]) == 25


@pytest.mark.asyncio
async def test_graph_requires_a_schema_two_index(adapter: type[_Adapter]) -> None:
    with pytest.raises(HTTPException) as exc:
        await _graph(_Database(schema_version=1))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "search_upgrade_required"  # type: ignore[index]
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_graph_reports_elasticsearch_outage(adapter: type[_Adapter]) -> None:
    adapter.unavailable = True

    with pytest.raises(HTTPException) as exc:
        await _graph(_Database())

    assert exc.value.status_code == 503


def test_graph_route_bounds_its_node_count_and_shares_the_search_filters() -> None:
    parameters = create_app().openapi()["paths"]["/api/v1/graph/entities"]["get"]["parameters"]
    names = [item["name"] for item in parameters]
    search_names = {
        item["name"]
        for item in create_app().openapi()["paths"]["/api/v1/search"]["get"]["parameters"]
    }

    assert names.count("entity_type") == 1
    assert search_names - {"sort", "limit", "cursor"} <= set(names)
    node_limit = next(item for item in parameters if item["name"] == "nodes")["schema"]
    assert (node_limit["maximum"], node_limit["minimum"]) == (MAX_NODES, 1)
