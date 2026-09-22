import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from event_fixtures import feed
from test_compare_postgres import _cluster
from test_monitor_api_postgres import _client, _user
from test_monitor_evaluation_elasticsearch import _index

from app.clustering.models import StoryClusterMember
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article, FeedArticle
from app.search import related
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3, ArticleDocument
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
AT = datetime(2026, 9, 1, 9, tzinfo=UTC)
# (title, feed description). Each related pair shares at least five non-stop terms of 3+ letters.
STRIKE = (
    "Harbor strike halts container shipping at the northern port",
    "Dock workers began a strike at the northern port, halting container shipping and stranding "
    "cargo vessels.",
)
SAME = (
    "Northern port strike stops container shipping",
    "Dock workers at the northern port walked out, stopping container shipping.",
)
LATE = (
    "Port strike leaves container ships waiting at the northern harbor",
    "Container shipping remains halted as the dock workers strike at the northern port continues.",
)
DELAYS = (
    "Container shipping delays spread after port strike",
    "Shipping lines report container delays as the dock strike continues.",
)
LIBRARY = (
    "Town council opens a renovated library branch",
    "The town council opened a renovated library branch downtown after months of construction.",
)
TERSE = ("Port update", "")
GHOST = (
    "Dock strike strands container vessels at the northern port",
    "Cargo vessels are stranded as the dock workers strike halts container shipping.",
)


async def _article(db, source, text: tuple[str, str], minutes: int) -> Article:  # type: ignore[no-untyped-def]
    title, description = text
    at = AT + timedelta(minutes=minutes)
    article = Article(
        id=uuid.uuid4(),
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title=title,
        normalized_title_hash=uuid.uuid4().hex,
        published_at=at,
        first_discovered_at=at,
    )
    db.add(article)
    await db.flush()
    db.add(
        FeedArticle(
            feed_id=source.id,
            article_id=article.id,
            guid=uuid.uuid4().hex,
            feed_title=title,
            feed_url=article.original_url,
            description=description or None,
            metadata_json={},
            discovered_at=at,
        )
    )
    await db.flush()
    return article


@pytest_asyncio.fixture(loop_scope="session")
async def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A controlled corpus, alone in a fresh schema 3 index.

    target, same  one story, indexed with its cluster: the index excludes `same`
    late          joins that story only after it was indexed: only PostgreSQL knows
    delays, echo  identical wording on another story, so their scores tie
    library       unrelated wording
    terse         too few terms to be like anything
    unindexed     in PostgreSQL only
    """
    name = f"articles-v3-related-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object, **_: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(related, "current_search_target", target)
    async with session_factory() as db, db.begin():
        source = await feed(db)
        w = SimpleNamespace(
            index=name,
            target=await _article(db, source, STRIKE, 0),
            same=await _article(db, source, SAME, 1),
            late=await _article(db, source, LATE, 2),
            delays=await _article(db, source, DELAYS, 3),
            echo=await _article(db, source, DELAYS, 4),
            library=await _article(db, source, LIBRARY, 5),
            terse=await _article(db, source, TERSE, 6),
            unindexed=await _article(db, source, STRIKE, 7),
        )
        w.cluster = await _cluster(db, [w.target, w.same], 1)
    await _index(name, [w.target.id, w.same.id, w.late.id, w.delays.id, w.echo.id,
                        w.library.id, w.terse.id])  # fmt: skip
    async with session_factory() as db, db.begin():
        db.add(
            StoryClusterMember(
                article_id=w.late.id, cluster_id=w.cluster.id, score=1, algorithm_version="rule-1"
            )
        )
    return w


async def _get(article_id: uuid.UUID, **params: Any) -> httpx.Response:
    async with _client(await _user()) as client:
        return await client.get(f"/api/v1/articles/{article_id}/related", params=params)


async def _related(article_id: uuid.UUID, **params: Any) -> dict[str, Any]:
    response = await _get(article_id, **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _ids(body: dict[str, Any]) -> list[str]:
    return [item["article"]["id"] for item in body["items"]]


async def _index_hits(w: SimpleNamespace, body: dict[str, Any]) -> list[str]:
    response = await ElasticsearchAdapter(get_settings().elasticsearch_url).search_index(
        w.index, body
    )
    return [hit["_source"]["article_id"] for hit in response["hits"]["hits"]]


async def test_related_coverage_is_similar_wording_outside_the_story(world: Any) -> None:
    w = world
    # The index alone still proposes `late`: its document predates the cluster change.
    proposed = await _index_hits(w, related.related_body(w.target.id, w.cluster.id))
    assert str(w.late.id) in proposed and str(w.same.id) not in proposed
    body = await _related(w.target.id)
    assert sorted(_ids(body)) == sorted([str(w.delays.id), str(w.echo.id)])
    assert body["skipped_stale"] == 1  # late, dropped by the PostgreSQL recheck
    assert {item["article"]["title"] for item in body["items"]} == {DELAYS[0]}


async def test_equal_scores_are_ordered_by_id_and_the_limit_applies(world: Any) -> None:
    w = world
    body = await _related(w.target.id)
    first, second = body["items"]
    assert first["score"] == second["score"]
    assert [first["article"]["id"], second["article"]["id"]] == sorted(
        [str(w.delays.id), str(w.echo.id)]
    )
    assert _ids(await _related(w.target.id, limit=1)) == [first["article"]["id"]]


async def test_a_projection_without_its_article_is_reported_not_returned(world: Any) -> None:
    w = world
    ghost = ArticleDocument(
        article_id=uuid.uuid4(),
        title=GHOST[0],
        descriptions=[GHOST[1]],
        body=None,
        published_at=AT,
        first_discovered_at=AT,
        content_available=False,
        processing_status=None,
        provenance=[],
    )
    async with httpx.AsyncClient(base_url=get_settings().elasticsearch_url) as es:
        response = await es.put(
            f"/{w.index}/_doc/{ghost.article_id}",
            params={"refresh": "true"},
            json=ghost.to_index_payload(schema_version=3),
        )
        assert response.status_code in (200, 201), response.text
    assert str(ghost.article_id) in await _index_hits(
        w, related.related_body(w.target.id, w.cluster.id)
    )
    body = await _related(w.target.id)
    assert sorted(_ids(body)) == sorted([str(w.delays.id), str(w.echo.id)])
    assert body["skipped_stale"] == 2  # late and the ghost


async def test_the_default_thresholds_would_find_nothing_in_a_small_corpus(world: Any) -> None:
    w = world
    body = related.related_body(w.target.id, None)
    similar = body["query"]["bool"]["must"][0]["more_like_this"]
    for key in related.SIMILARITY:
        del similar[key]
    assert await _index_hits(w, body) == []
    assert await _index_hits(w, related.related_body(w.target.id, None)) != []


async def test_text_poor_unindexed_and_unknown_articles(world: Any) -> None:
    w = world
    assert (await _related(w.terse.id))["items"] == []  # shares "port" with four articles
    assert (await _related(w.unindexed.id))["items"] == []  # same words, but not in the index
    assert (await _related(w.library.id))["items"] == []
    assert (await _get(uuid.uuid4())).status_code == 404
    assert (await _get(w.target.id, limit=21)).status_code == 422
    assert (await _get(w.target.id, limit=0)).status_code == 422
