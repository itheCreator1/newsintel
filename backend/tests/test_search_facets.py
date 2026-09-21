from typing import Any

import pytest
from fastapi import HTTPException
from test_search_timeline import _Adapter, _criteria, _Database

from app.core.config import Settings
from app.main import create_app
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.facets import (
    GROUPS,
    MAX_FACET_BUCKETS,
    PartialResponse,
    facets_body,
    parse_facets,
)
from app.search.routes import search_facet_counts

QUERY: dict[str, Any] = {"bool": {"must": [{"match_all": {}}], "filter": []}}
ROOT_ORDER = [{"articles": "desc"}, {"_key": "asc"}]


def test_flat_groups_are_plain_terms_ordered_by_count_then_key() -> None:
    body = facets_body(QUERY, 10)
    assert body["size"] == 0
    assert body["track_total_hits"] is True
    assert body["query"] == QUERY
    for name, field in [
        ("story_countries", "primary_story_country"),
        ("mentioned_countries", "mentioned_countries"),
        ("languages", "detected_language"),
        ("keywords", "keyword_ids"),  # `keywords` is an object field, not nested
        ("story_clusters", "story_cluster_id"),
    ]:
        assert body["aggs"][name] == {
            "terms": {"field": field, "size": 10, "order": [{"_count": "desc"}, {"_key": "asc"}]}
        }


def test_nested_groups_count_and_rank_root_articles() -> None:
    body = facets_body(QUERY, 10)
    for name, path, field in [
        ("sources", "provenance", "provenance.source_id"),
        ("source_countries", "provenance", "provenance.source_country"),
        ("entities", "entities", "entities.id"),
        ("entity_types", "entities", "entities.type"),
    ]:
        assert body["aggs"][name] == {
            "nested": {"path": path},
            "aggs": {
                "top": {
                    "terms": {"field": field, "size": 10, "order": ROOT_ORDER},
                    "aggs": {"articles": {"reverse_nested": {}}},
                }
            },
        }


def test_every_group_is_capped_at_the_absolute_bucket_limit() -> None:
    body = facets_body(QUERY, 10_000)
    sizes = [
        (agg.get("aggs", {}).get("top") or agg)["terms"]["size"] for agg in body["aggs"].values()
    ]
    assert sizes == [MAX_FACET_BUCKETS] * len(GROUPS)


def test_exact_top_n_counts_depend_on_a_single_primary_shard() -> None:
    # Multi-shard terms aggregation is approximate; facet exactness assumes this setting.
    assert ARTICLE_INDEX_SETTINGS_V3["settings"]["number_of_shards"] == 1


def _response(**aggregations: Any) -> dict[str, Any]:
    return {
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0},
        "hits": {"total": {"value": 3, "relation": "eq"}, "hits": []},
        "aggregations": aggregations,
    }


def test_parse_reads_root_counts_for_nested_groups_and_doc_counts_for_flat_ones() -> None:
    total, groups = parse_facets(
        _response(
            entity_types={
                "doc_count": 5,
                "top": {
                    "sum_other_doc_count": 0,
                    "buckets": [{"key": "ORG", "doc_count": 3, "articles": {"doc_count": 1}}],
                },
            },
            languages={"sum_other_doc_count": 4, "buckets": [{"key": "en", "doc_count": 2}]},
        )
    )
    assert total == 3
    assert groups["entity_types"].buckets == [("ORG", 1)]  # one article, not three records
    assert groups["entity_types"].truncated is False
    assert groups["languages"].buckets == [("en", 2)]
    assert groups["languages"].truncated is True
    assert groups["sources"].buckets == []  # absent aggregation reads as empty


@pytest.mark.parametrize(
    "broken",
    [{"timed_out": True}, {"_shards": {"total": 1, "successful": 0, "failed": 1}}],
)
def test_partial_responses_are_rejected_not_undercounted(broken: dict[str, Any]) -> None:
    with pytest.raises(PartialResponse):
        parse_facets({**_response(), **broken})
    assert issubclass(PartialResponse, ElasticsearchUnavailable)  # routes map it to 503


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> type[_Adapter]:
    _Adapter.responses, _Adapter.bodies, _Adapter.unavailable = [], [], False
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    return _Adapter


async def _facets(schema_version: int = 3) -> object:
    return await search_facet_counts(
        _Database(schema_version),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Settings(),
        _criteria(),
        10,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_version", [1, 2])
async def test_facets_require_a_schema_three_index(
    adapter: type[_Adapter], schema_version: int
) -> None:
    with pytest.raises(HTTPException) as exc:
        await _facets(schema_version)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "search_upgrade_required"  # type: ignore[index]
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_facets_report_elasticsearch_outage(adapter: type[_Adapter]) -> None:
    adapter.unavailable = True
    with pytest.raises(HTTPException) as exc:
        await _facets()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_facets_report_a_partial_response_as_unavailable(adapter: type[_Adapter]) -> None:
    adapter.responses = [{**_response(), "timed_out": True}]
    with pytest.raises(HTTPException) as exc:
        await _facets()
    assert exc.value.status_code == 503


def test_facets_route_shares_search_parameters_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    params = paths["/api/v1/search/facets"]["get"]["parameters"]
    search_names = {item["name"] for item in paths["/api/v1/search"]["get"]["parameters"]}
    assert {item["name"] for item in params} == search_names - {"sort", "cursor"}
    limit = next(item for item in params if item["name"] == "limit")["schema"]
    assert (limit["default"], limit["maximum"]) == (10, MAX_FACET_BUCKETS)
