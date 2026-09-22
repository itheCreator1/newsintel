from typing import Any

from app.geo.investigation import (
    MAX_COUNTRIES,
    countries_body,
    in_country,
    located,
    parse_countries,
)

QUERY: dict[str, Any] = {"match_all": {}}
STORIES = {"cardinality": {"field": "story_cluster_id", "precision_threshold": 3000}}
STORY_RESPONSE: dict[str, Any] = {
    "hits": {"total": {"value": 9, "relation": "eq"}},
    "aggregations": {
        "countries": {
            "buckets": [
                {"key": "GR", "doc_count": 4, "stories": {"value": 3}},
                {"key": "US", "doc_count": 2, "stories": {"value": 2}},
            ]
        },
        "located": {"doc_count": 5},
    },
}


def test_story_and_mentioned_count_documents_by_their_own_field_and_estimate_stories() -> None:
    body = countries_body("mentioned", QUERY)
    assert (body["size"], body["track_total_hits"], body["query"]) == (0, True, QUERY)
    countries = body["aggs"]["countries"]
    assert countries["terms"] == {
        "field": "mentioned_countries",
        "size": MAX_COUNTRIES,
        "order": [{"_count": "desc"}, {"_key": "asc"}],
    }
    assert countries["aggs"] == {"stories": STORIES}
    assert body["aggs"]["located"] == {"filter": {"exists": {"field": "mentioned_countries"}}}
    story = countries_body("story", QUERY)["aggs"]["countries"]["terms"]
    assert story["field"] == "primary_story_country"


def test_source_counts_root_articles_under_the_nested_feeds() -> None:
    body = countries_body("source", QUERY)
    countries = body["aggs"]["countries"]
    assert countries["nested"] == {"path": "provenance"}
    top = countries["aggs"]["top"]
    assert top["terms"] == {
        "field": "provenance.source_country",
        "size": 300,
        "order": [{"articles": "desc"}, {"_key": "asc"}],
    }
    assert top["aggs"]["articles"] == {"reverse_nested": {}, "aggs": {"stories": STORIES}}
    assert top["aggs"]["sources"] == {
        "cardinality": {"field": "provenance.source_id", "precision_threshold": 3000}
    }
    assert body["aggs"]["located"] == {"filter": located("source")}
    assert located("source") == {
        "nested": {
            "path": "provenance",
            "query": {"exists": {"field": "provenance.source_country"}},
        }
    }


def test_parse_reads_exact_articles_and_coverage_and_estimated_stories() -> None:
    coverage, items = parse_countries("story", STORY_RESPONSE)
    assert (coverage.unit, coverage.window_total, coverage.located) == ("articles", 9, 5)
    assert [(i.country_code, i.articles, i.stories, i.sources, i.events) for i in items] == [
        ("GR", 4, 3, None, None),
        ("US", 2, 2, None, None),
    ]


def test_parse_source_takes_articles_from_the_root_not_the_feed_records() -> None:
    response = {
        "hits": {"total": {"value": 3}},
        "aggregations": {
            "countries": {
                "doc_count": 7,
                "top": {
                    "buckets": [
                        {
                            "key": "US",
                            "doc_count": 5,  # feed records: one article can hold two US feeds
                            "articles": {"doc_count": 3, "stories": {"value": 2}},
                            "sources": {"value": 2},
                        }
                    ]
                },
            },
            "located": {"doc_count": 3},
        },
    }
    _, items = parse_countries("source", response)
    assert [(i.country_code, i.articles, i.stories, i.sources) for i in items] == [("US", 3, 2, 2)]


def test_an_empty_index_parses_to_no_countries() -> None:
    coverage, items = parse_countries("story", {"hits": {"total": {"value": 0}}})
    assert (coverage.window_total, coverage.located, items) == (0, 0, [])


def test_evidence_filters_on_the_role_field() -> None:
    assert in_country("story", "GR") == {"term": {"primary_story_country": "GR"}}
    assert in_country("mentioned", "GR") == {"term": {"mentioned_countries": "GR"}}
    assert in_country("source", "GR") == {
        "nested": {"path": "provenance", "query": {"term": {"provenance.source_country": "GR"}}}
    }
