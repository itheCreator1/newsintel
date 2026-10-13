import uuid

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.related import CANDIDATES, related_body
from app.search.routes import related_coverage

ARTICLE = uuid.UUID("11111111-1111-4111-8111-111111111111")
CLUSTER = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_the_body_asks_for_similar_wording_outside_the_article_and_its_story() -> None:
    body = related_body(ARTICLE, CLUSTER)
    assert body["size"] == CANDIDATES == 100
    assert body["query"]["bool"]["must_not"] == [
        {"term": {"article_id": str(ARTICLE)}},
        {"term": {"story_cluster_id": str(CLUSTER)}},
    ]
    (similar,) = body["query"]["bool"]["must"]
    assert similar["more_like_this"]["like"] == [{"_id": str(ARTICLE)}]
    assert similar["more_like_this"]["fields"] == ["title", "descriptions", "body"]
    assert body["sort"] == [{"_score": "desc"}, {"article_id": "asc"}]
    assert (body["_source"], body["track_total_hits"]) == (["article_id"], False)


def test_an_article_outside_any_story_excludes_only_itself() -> None:
    assert related_body(ARTICLE, None)["query"]["bool"]["must_not"] == [
        {"term": {"article_id": str(ARTICLE)}}
    ]


def test_the_similarity_thresholds_are_explicit() -> None:
    similar = related_body(ARTICLE, None)["query"]["bool"]["must"][0]["more_like_this"]
    keys = (
        "min_term_freq",
        "min_doc_freq",
        "max_query_terms",
        "min_word_length",
        "minimum_should_match",
    )
    assert {key: similar[key] for key in keys} == {
        "min_term_freq": 1,
        "min_doc_freq": 1,
        "max_query_terms": 25,
        "min_word_length": 3,
        "minimum_should_match": "5",
    }  # fmt: skip
    assert {"the", "and", "with"} <= set(similar["stop_words"])


def test_greek_stop_words_are_left_out_in_every_written_form() -> None:
    # The standard analyzer keeps accents and turns a capital final Σ into σ ("ΤΟΥΣ" is "τουσ").
    stop_words = set(
        related_body(ARTICLE, None)["query"]["bool"]["must"][0]["more_like_this"]["stop_words"]
    )
    assert {"και", "από", "απο", "τους", "τουσ", "στην"} <= stop_words


@pytest.mark.asyncio
async def test_unavailable_search_is_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_args: object) -> None:
        raise ElasticsearchUnavailable("down")

    monkeypatch.setattr("app.search.routes.related_articles", down)
    with pytest.raises(HTTPException) as caught:
        await related_coverage(ARTICLE, object(), object(), Settings(), 8)  # type: ignore[arg-type]
    assert caught.value.status_code == 503
