import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.clustering.models import StoryCluster
from app.core.config import Settings
from app.feeds.models import Article
from app.graph.routes import edge_evidence
from app.graph.schemas import EdgeEvidenceResponse
from app.graph.service import (
    MAX_EVIDENCE,
    decode_after,
    encode_after,
    evidence_body,
    focus_query,
    parse_evidence,
)
from app.nlp.models import Entity
from app.search.criteria import SearchCriteria, build_query
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.query import parse_query

LEFT, RIGHT = uuid.uuid4(), uuid.uuid4()
DAY = datetime(2026, 9, 14, 12, tzinfo=UTC)


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


def _holds(entity_id: uuid.UUID) -> dict[str, Any]:
    return {"nested": {"path": "entities", "query": {"term": {"entities.id": str(entity_id)}}}}


def _hit(article_id: uuid.UUID, sort: list[Any]) -> dict[str, Any]:
    return {"_source": {"article_id": str(article_id)}, "sort": sort}


def _response(
    hits: list[dict[str, Any]],
    total: int | None = None,
    clusters: list[tuple[uuid.UUID, int]] | None = None,
    distinct_clusters: int = 0,
    first: str | None = "2026-09-10T08:00:00.000Z",
    last: str | None = "2026-09-14T12:00:00.000Z",
) -> dict[str, Any]:
    return {
        "hits": {"total": {"value": len(hits) if total is None else total}, "hits": hits},
        "aggregations": {
            "first": {"value_as_string": first},
            "last": {"value_as_string": last},
            "cluster_count": {"value": distinct_clusters},
            "clusters": {
                "buckets": [{"key": str(key), "doc_count": count} for key, count in clusters or []]
            },
        },
    }


# --- query body ---------------------------------------------------------------------


def test_evidence_body_reuses_the_graph_query_and_requires_both_entities() -> None:
    query = focus_query(build_query(_criteria("grid", languages=["en"]), 2), None)

    body = evidence_body(query, LEFT, RIGHT, limit=10, after=None)

    assert body["query"] == {"bool": {"filter": [query, _holds(LEFT), _holds(RIGHT)]}}
    assert body["track_total_hits"] is True
    assert body["size"] == 11  # one extra hit reveals whether another page exists
    assert body["sort"] == [{"effective_date": "desc"}, {"article_id": "asc"}]
    assert body["_source"] == ["article_id"]
    assert "search_after" not in body
    assert body["aggs"]["first"] == {"min": {"field": "effective_date"}}
    assert body["aggs"]["last"] == {"max": {"field": "effective_date"}}
    assert body["aggs"]["cluster_count"] == {"cardinality": {"field": "story_cluster_id"}}
    assert body["aggs"]["clusters"] == {"terms": {"field": "story_cluster_id", "size": 10}}


def test_evidence_body_continues_after_the_previous_page() -> None:
    body = evidence_body({"match_all": {}}, LEFT, RIGHT, limit=5, after=[123, "abc"])

    assert body["search_after"] == [123, "abc"]


def test_evidence_body_never_asks_for_more_than_the_absolute_cap() -> None:
    body = evidence_body({"match_all": {}}, LEFT, RIGHT, limit=10_000, after=None)

    assert body["size"] == MAX_EVIDENCE + 1


# --- parsing and cursors ------------------------------------------------------------


def test_parse_evidence_reads_totals_dates_clusters_and_the_next_cursor() -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    cluster = uuid.uuid4()
    response = _response(
        [_hit(ids[0], [3, "a"]), _hit(ids[1], [2, "b"]), _hit(ids[2], [1, "c"])],
        total=7,
        clusters=[(cluster, 4)],
        distinct_clusters=2,
    )

    parsed = parse_evidence(response, limit=2)

    assert parsed.total == 7
    assert parsed.article_ids == ids[:2]
    assert parsed.next_after == [2, "b"]
    assert parsed.first_at == datetime(2026, 9, 10, 8, tzinfo=UTC)
    assert parsed.last_at == DAY
    assert parsed.cluster_count == 2
    assert parsed.cluster_counts == [(cluster, 4)]


def test_parse_evidence_has_no_cursor_on_the_last_page() -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]

    parsed = parse_evidence(_response([_hit(ids[0], [2, "a"]), _hit(ids[1], [1, "b"])]), limit=2)

    assert parsed.article_ids == ids
    assert parsed.next_after is None


def test_parse_evidence_without_any_co_occurrence_is_empty_not_an_error() -> None:
    parsed = parse_evidence(_response([], first=None, last=None), limit=10)

    assert (parsed.total, parsed.article_ids, parsed.first_at, parsed.last_at) == (
        0,
        [],
        None,
        None,
    )


def test_cursor_round_trips_and_rejects_garbage() -> None:
    assert decode_after(encode_after([1789041600000, "abc"])) == [1789041600000, "abc"]
    for bad in ["not base64!", encode_after([1])[:-2], "e30=", "W10="]:
        with pytest.raises(ValueError):
            decode_after(bad)


# --- route --------------------------------------------------------------------------


class _Target:
    index_name = "articles-v2-test"

    def __init__(self, schema_version: int) -> None:
        self.schema_version = schema_version


class _Database:
    def __init__(self, schema_version: int = 2, entities: list[Entity] | None = None) -> None:
        self.target = _Target(schema_version)
        self.entities = {entity.id: entity for entity in entities or []}

    async def scalar(self, _query: object) -> _Target:
        return self.target

    async def get(self, _model: object, entity_id: uuid.UUID) -> Entity | None:
        return self.entities.get(entity_id)


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


def _article(article_id: uuid.UUID, title: str) -> Article:
    return Article(
        id=article_id,
        original_url=f"https://example.test/{article_id}",
        normalized_url=f"https://example.test/{article_id}",
        title=title,
        published_at=DAY,
        first_discovered_at=DAY,
    )


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


def _database(**kwargs: Any) -> _Database:
    return _Database(
        entities=[_entity(LEFT, "Harbour Authority"), _entity(RIGHT, "Ada Reyes", "PERSON")],
        **kwargs,
    )


async def _evidence(
    database: _Database,
    criteria: SearchCriteria | None = None,
    *,
    source: uuid.UUID = LEFT,
    target: uuid.UUID = RIGHT,
    focus_entity_id: uuid.UUID | None = None,
    limit: int = 10,
    cursor: str | None = None,
) -> EdgeEvidenceResponse:
    return await edge_evidence(
        database,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Settings(),
        criteria or _criteria(),
        source,
        target,
        focus_entity_id,
        limit,
        cursor,
    )


@pytest.fixture
def hydrated(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[uuid.UUID, Any]]:
    stores: dict[str, dict[uuid.UUID, Any]] = {"articles": {}, "clusters": {}}

    async def articles(_db: object, ids: list[uuid.UUID]) -> dict[uuid.UUID, Article]:
        return {value: stores["articles"][value] for value in ids if value in stores["articles"]}

    async def clusters(_db: object, ids: list[uuid.UUID]) -> dict[uuid.UUID, StoryCluster]:
        return {value: stores["clusters"][value] for value in ids if value in stores["clusters"]}

    monkeypatch.setattr("app.graph.service.articles_by_id", articles)
    monkeypatch.setattr("app.graph.service.clusters_by_id", clusters)
    return stores


@pytest.mark.asyncio
async def test_evidence_lists_canonical_articles_and_stories(
    adapter: type[_Adapter], hydrated: dict[str, dict[uuid.UUID, Any]]
) -> None:
    first, second, representative, cluster_id = (uuid.uuid4() for _ in range(4))
    hydrated["articles"] = {
        first: _article(first, "Harbour Authority hires Ada Reyes"),
        second: _article(second, "Ada Reyes leads harbour talks"),
        representative: _article(representative, "Harbour leadership change"),
    }
    hydrated["clusters"] = {
        cluster_id: StoryCluster(
            id=cluster_id,
            algorithm_version="v1",
            article_count=5,
            source_count=3,
            representative_article_id=representative,
        )
    }
    adapter.responses = [
        _response(
            [_hit(first, [2, "a"]), _hit(second, [1, "b"])],
            clusters=[(cluster_id, 2)],
            distinct_clusters=1,
        )
    ]
    criteria = _criteria("harbour", languages=["en"])

    result = await _evidence(_database(), criteria)

    assert (result.source.id, result.source.text, result.source.type) == (
        LEFT,
        "Harbour Authority",
        "ORG",
    )
    assert (result.target.id, result.target.text) == (RIGHT, "Ada Reyes")
    assert "co-occurrence" in result.meaning
    assert (result.article_count, result.cluster_count) == (2, 1)
    assert result.first_at == datetime(2026, 9, 10, 8, tzinfo=UTC)
    assert [article.id for article in result.articles] == [first, second]
    assert result.next_cursor is None
    assert result.missing_from_archive == 0
    (story,) = result.clusters
    assert (story.id, story.edge_article_count, story.article_count, story.source_count) == (
        cluster_id,
        2,
        5,
        3,
    )
    assert story.representative_article is not None
    assert story.representative_article.id == representative
    ((index_name, body),) = adapter.bodies
    assert index_name == "articles-v2-test"
    assert body["query"]["bool"]["filter"][0] == build_query(criteria, 2)


@pytest.mark.asyncio
async def test_evidence_applies_the_graph_focus_so_counts_match_the_drawn_weight(
    adapter: type[_Adapter], hydrated: dict[str, dict[uuid.UUID, Any]]
) -> None:
    focus = uuid.uuid4()
    adapter.responses = [_response([])]

    await _evidence(_database(), focus_entity_id=focus)

    expected = focus_query(build_query(_criteria(), 2), focus)
    assert adapter.bodies[0][1]["query"]["bool"]["filter"][0] == expected


@pytest.mark.asyncio
async def test_evidence_pages_with_an_opaque_cursor(
    adapter: type[_Adapter], hydrated: dict[str, dict[uuid.UUID, Any]]
) -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    hydrated["articles"] = {
        value: _article(value, f"Story {index}") for index, value in enumerate(ids)
    }
    adapter.responses = [
        _response(
            [_hit(ids[0], [3, "a"]), _hit(ids[1], [2, "b"]), _hit(ids[2], [1, "c"])], total=3
        ),
        _response([_hit(ids[2], [1, "c"])], total=3),
    ]

    page = await _evidence(_database(), limit=2)
    assert page.next_cursor is not None
    assert [article.id for article in page.articles] == ids[:2]
    follow = await _evidence(_database(), limit=2, cursor=page.next_cursor)

    assert adapter.bodies[1][1]["search_after"] == [2, "b"]
    assert [article.id for article in follow.articles] == [ids[2]]
    assert follow.next_cursor is None


@pytest.mark.asyncio
async def test_evidence_drops_and_counts_articles_the_archive_no_longer_holds(
    adapter: type[_Adapter], hydrated: dict[str, dict[uuid.UUID, Any]]
) -> None:
    kept, stale = uuid.uuid4(), uuid.uuid4()
    hydrated["articles"] = {kept: _article(kept, "Still here")}
    adapter.responses = [_response([_hit(kept, [2, "a"]), _hit(stale, [1, "b"])])]

    result = await _evidence(_database())

    assert [article.id for article in result.articles] == [kept]
    assert result.missing_from_archive == 1


@pytest.mark.asyncio
async def test_evidence_for_a_pair_that_never_co_occurs_is_empty_not_an_error(
    adapter: type[_Adapter], hydrated: dict[str, dict[uuid.UUID, Any]]
) -> None:
    adapter.responses = [_response([], first=None, last=None)]

    result = await _evidence(_database())

    assert (result.article_count, result.cluster_count, result.articles, result.clusters) == (
        0,
        0,
        [],
        [],
    )
    assert result.first_at is None and result.last_at is None


@pytest.mark.asyncio
async def test_evidence_rejects_an_edge_from_an_entity_to_itself(adapter: type[_Adapter]) -> None:
    with pytest.raises(HTTPException) as exc:
        await _evidence(_database(), target=LEFT)

    assert exc.value.status_code == 422
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_evidence_reports_an_unknown_entity(adapter: type[_Adapter]) -> None:
    with pytest.raises(HTTPException) as exc:
        await _evidence(_database(), target=uuid.uuid4())

    assert exc.value.status_code == 404
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_evidence_rejects_a_malformed_cursor(adapter: type[_Adapter]) -> None:
    with pytest.raises(HTTPException) as exc:
        await _evidence(_database(), cursor="garbage!")

    assert exc.value.status_code == 400
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_evidence_requires_a_schema_two_index(adapter: type[_Adapter]) -> None:
    with pytest.raises(HTTPException) as exc:
        await _evidence(_database(schema_version=1))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "search_upgrade_required"  # type: ignore[index]
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_evidence_reports_elasticsearch_outage(adapter: type[_Adapter]) -> None:
    adapter.unavailable = True

    with pytest.raises(HTTPException) as exc:
        await _evidence(_database())

    assert exc.value.status_code == 503
