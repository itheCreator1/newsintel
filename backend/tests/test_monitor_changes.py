import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.main import create_app
from app.monitors.changes import (
    MATERIAL_SOURCE_GROWTH,
    Found,
    Labels,
    build_changes,
    parse_found,
    prior_body,
    window_agg_body,
)
from app.monitors.schemas import ChangeEvidence

UPTO = datetime(2026, 9, 20, 12, tzinfo=UTC)
AFTER = UPTO - timedelta(hours=1)
QUERY = {"match_all": {}}


def _id() -> str:
    return str(uuid.uuid4())


def _evidence(title: str = "Grid failure") -> ChangeEvidence:
    return ChangeEvidence(article_id=uuid.uuid4(), title=title)


def _bucket(key: str, count: int, *, nested: bool, titles: tuple[str, ...] = ("A",)) -> dict:
    hits = {"hits": {"hits": [{"_source": {"article_id": _id(), "title": t}} for t in titles]}}
    if nested:
        return {"key": key, "doc_count": count, "articles": {"doc_count": count, "hits": hits}}
    return {"key": key, "doc_count": count, "hits": hits}


def _agg(buckets: list[dict], other: int = 0) -> dict:
    return {"keys": {"buckets": buckets, "sum_other_doc_count": other}}


def test_window_body_is_bounded_and_asks_for_top_ten_with_evidence_per_kind() -> None:
    body = window_agg_body(QUERY, AFTER, UPTO, 3)

    bounds = body["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]
    assert bounds == {"gt": AFTER.isoformat(), "lte": UPTO.isoformat()}
    assert body["size"] == 0 and body["track_total_hits"] is True
    assert set(body["aggs"]) == {"sources", "entities", "stories"}
    sources = body["aggs"]["sources"]
    assert sources["nested"] == {"path": "provenance"}
    terms = sources["aggs"]["keys"]["terms"]
    assert terms["field"] == "provenance.source_id" and terms["size"] == 10
    assert terms["order"] == [{"_count": "desc"}, {"_key": "asc"}]
    hits = sources["aggs"]["keys"]["aggs"]["articles"]
    assert "reverse_nested" in hits and hits["aggs"]["hits"]["top_hits"]["size"] == 3
    stories = body["aggs"]["stories"]["aggs"]["keys"]
    assert stories["terms"]["field"] == "story_cluster_id"
    assert stories["aggs"]["hits"]["top_hits"]["sort"][0] == {"first_discovered_at": "desc"}


@pytest.mark.parametrize(
    ("version", "kinds"),
    [(1, {"sources"}), (2, {"sources", "entities"}), (3, {"sources", "entities", "stories"})],
)
def test_kinds_the_index_cannot_answer_are_not_asked_for(version: int, kinds: set[str]) -> None:
    assert set(window_agg_body(QUERY, AFTER, UPTO, version)["aggs"]) == kinds
    assert set(prior_body(QUERY, AFTER, {k: ["x"] for k in kinds}, version)["aggs"]) == kinds


def test_prior_body_looks_only_at_candidates_up_to_the_viewed_boundary() -> None:
    body = prior_body(QUERY, AFTER, {"sources": ["a", "b"], "entities": [], "stories": ["c"]}, 3)

    bounds = body["query"]["bool"]["filter"][1]["range"]["first_discovered_at"]
    assert bounds == {"lte": AFTER.isoformat()}
    assert set(body["aggs"]) == {"sources", "stories"}  # nothing to look for, nothing asked
    terms = body["aggs"]["sources"]["aggs"]["keys"]["terms"]
    assert terms["include"] == ["a", "b"] and terms["size"] == 2
    assert "aggs" not in body["aggs"]["sources"]["aggs"]["keys"]


def test_parse_found_reads_nested_and_plain_buckets_and_the_overflow_flag() -> None:
    response = {
        "aggregations": {
            "sources": _agg([_bucket("s1", 4, nested=True, titles=("X", "Y"))], other=2),
            "stories": _agg([_bucket("c1", 3, nested=False)]),
        }
    }

    sources, more = parse_found(response, "sources")
    assert more is True and sources[0].key == "s1" and sources[0].count == 4
    assert [e.title for e in sources[0].evidence] == ["X", "Y"]
    stories, more = parse_found(response, "stories")
    assert more is False and stories[0].count == 3
    assert parse_found(response, "entities") == ([], False)


def _labels(**kwargs: Any) -> Labels:
    return Labels(
        feeds=kwargs.get("feeds", {}),
        entities=kwargs.get("entities", {}),
        clusters=kwargs.get("clusters", {}),
    )


def _found(key: str, count: int = 2) -> Found:
    return Found(key, count, [_evidence()])


def test_only_never_matched_sources_and_entities_are_new_and_unlabelled_ones_are_dropped() -> None:
    s1, s2, s3, e1, e2 = _id(), _id(), _id(), _id(), _id()
    changes = build_changes(
        AFTER, UPTO, 9,
        sources=([_found(s1), _found(s2), _found(s3)], False),
        entities=([_found(e1), _found(e2)], True),
        stories=([], False),
        seen={"sources": {s2}, "entities": set(), "stories": set()},
        growth={},
        labels=_labels(feeds={s1: "Wire", s2: "Daily"}, entities={e1: ("Acme", "ORG")}),
    )  # fmt: skip

    assert (
        changes.article_count == 9 and changes.window_start == AFTER and changes.window_end == UPTO
    )
    assert [(s.name, s.article_count) for s in changes.sources] == [
        ("Wire", 2)
    ]  # s2 seen, s3 unlabelled
    assert [(e.name, e.entity_type) for e in changes.entities] == [("Acme", "ORG")]
    assert (changes.more_sources, changes.more_entities, changes.more_stories) == (
        False,
        True,
        False,
    )


def test_a_story_is_new_or_grew_by_the_material_threshold_and_never_by_the_cached_count() -> None:
    new, grew, small, gone = _id(), _id(), _id(), _id()
    assert MATERIAL_SOURCE_GROWTH == 2
    changes = build_changes(
        AFTER, UPTO, 5,
        sources=([], False), entities=([], False),
        stories=([_found(new), _found(grew), _found(small), _found(gone)], False),
        seen={"sources": set(), "entities": set(), "stories": {grew, small, gone}},
        growth={new: (0, 3), grew: (3, 5), small: (3, 4), gone: (1, 9)},
        labels=_labels(clusters={new: "Harbour fire", grew: "Grid failure", small: "Quiet"}),
    )  # fmt: skip

    assert [(s.title, s.status, s.source_count, s.sources_added) for s in changes.stories] == [
        ("Harbour fire", "new", 3, 3),
        ("Grid failure", "grew", 5, 2),
    ]  # `small` grew by 1, `gone` no longer exists


def test_the_changes_route_is_documented_without_csrf() -> None:
    operation = create_app().openapi()["paths"]["/api/v1/monitors/{monitor_id}/changes"]["get"]

    assert not [p for p in operation.get("parameters", []) if p["name"] == "X-CSRF-Token"]
