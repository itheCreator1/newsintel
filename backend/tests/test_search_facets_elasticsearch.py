import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from test_clustering_postgres import _article, _feed
from test_monitor_api_postgres import _client, _user

from app.clustering.models import StoryCluster
from app.core.config import get_settings
from app.db.session import session_factory
from app.nlp.models import Entity, Keyword
from app.search import routes
from app.search.documents import (
    ARTICLE_INDEX_SETTINGS_V3,
    ArticleDocument,
    EntityDocument,
    KeywordDocument,
    ProvenanceDocument,
)
from app.search.elasticsearch import BulkDocument, ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def index_name(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"articles-v3-facets-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object, **_: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(routes, "current_search_target", target)
    return name


def _entity(item: Entity) -> EntityDocument:
    return EntityDocument(item.id, item.entity_type, item.display_text, item.normalized_text)


async def _seed(index_name: str, word: str) -> dict[str, Any]:
    async with session_factory() as db, db.begin():
        f1, f2 = await _feed(db, "15B Wire"), await _feed(db, "15B Daily")
        e1 = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=f"ada {word}",
            display_text="Ada Reyes",
        )
        e2 = Entity(
            language="en",
            entity_type="ORG",
            normalized_text=f"harbour {word}",
            display_text="Harbour Authority",
        )
        e3 = Entity(
            language="en", entity_type="ORG", normalized_text=f"grid {word}", display_text="Grid Co"
        )
        k1 = Keyword(
            language="en", kind="keyphrase", normalized_text=f"tariff {word}", display_text="Tariff"
        )
        db.add_all([e1, e2, e3, k1])
        representative = await _article(
            db, feeds=[f1], title=f"{word} lead", title_hash=uuid.uuid4().hex
        )
        cluster = StoryCluster(
            algorithm_version="v1",
            article_count=2,
            source_count=2,
            representative_article_id=representative.id,
        )
        db.add(cluster)
        await db.flush()
    ghost = EntityDocument(uuid.uuid4(), "MISC", "Ghost", f"ghost {word}")
    us1, us2 = ProvenanceDocument(f1.id, f1.name, "US"), ProvenanceDocument(f2.id, f2.name, "US")
    now = datetime.now(UTC)

    def doc(title: str, provenance: list[ProvenanceDocument], **fields: Any) -> ArticleDocument:
        return ArticleDocument(
            article_id=uuid.uuid4(),
            title=title,
            descriptions=[],
            body=None,
            published_at=now,
            first_discovered_at=now,
            content_available=False,
            processing_status="succeeded",
            provenance=provenance,
            **fields,
        )

    documents = [
        doc(
            f"{word} one",
            [us1, us2],
            entities=[_entity(e2), _entity(e3)],
            keywords=[KeywordDocument(k1.id, k1.kind, k1.display_text, k1.normalized_text)],
            detected_language="en",
            primary_story_country="GR",
            mentioned_countries=["GR", "US"],
            story_cluster_id=cluster.id,
            cluster_source_count=2,
        ),
        doc(
            f"{word} two",
            [us1],
            entities=[_entity(e1)],
            detected_language="en",
            primary_story_country="GR",
            story_cluster_id=cluster.id,
            cluster_source_count=2,
        ),
        doc(
            f"{word} three",
            [us2],
            entities=[_entity(e1), ghost],
            detected_language="de",
        ),
        doc("unrelated", [us1], entities=[_entity(e1)], detected_language="en"),
    ]
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    for batch in adapter.build_batches(
        index_name,
        [
            BulkDocument(str(item.article_id), 1, item.to_index_payload(schema_version=3))
            for item in documents
        ],
    ):
        assert all(result.error is None for result in await adapter.bulk(batch))
    await adapter.refresh(index_name)
    return {
        "f1": f1,
        "f2": f2,
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "k1": k1,
        "cluster": cluster,
        "representative": representative,
    }


def _pairs(group: dict[str, Any]) -> list[tuple[str, int]]:
    return [(bucket["value"], bucket["count"]) for bucket in group["buckets"]]


async def _get(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/api/v1/search/facets", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_facets_count_matching_root_articles_with_postgres_labels(index_name: str) -> None:
    word = f"facetar{uuid.uuid4().hex[:8]}"
    seeded = await _seed(index_name, word)
    f1, f2 = str(seeded["f1"].id), str(seeded["f2"].id)
    e1, e2, e3 = (str(seeded[key].id) for key in ("e1", "e2", "e3"))

    async with _client(await _user()) as client:
        body = await _get(client, q=word)
        assert body["total"] == 3
        assert _pairs(body["sources"]) == sorted([(f1, 2), (f2, 2)], key=lambda p: p[0])
        assert {b["value"]: b["label"] for b in body["sources"]["buckets"]} == {
            f1: "15B Wire",
            f2: "15B Daily",
        }
        # A1 carries two US provenance rows; it is still one article.
        assert _pairs(body["source_countries"]) == [("US", 3)]
        assert _pairs(body["story_countries"]) == [("GR", 2)]
        assert _pairs(body["mentioned_countries"]) == [("GR", 1), ("US", 1)]
        assert _pairs(body["languages"]) == [("en", 2), ("de", 1)]
        # The ghost entity has no catalogue row, so it is dropped rather than shown unlabelled.
        assert _pairs(body["entities"]) == [(e1, 2), *sorted([(e2, 1), (e3, 1)])]
        assert body["entities"]["buckets"][0]["label"] == "Ada Reyes"
        # A1's two ORG entities count once; MISC comes from the uncatalogued entity.
        assert _pairs(body["entity_types"]) == [("PERSON", 2), ("MISC", 1), ("ORG", 1)]
        assert body["entity_types"]["buckets"][0]["label"] is None
        assert [(b["value"], b["label"], b["count"]) for b in body["keywords"]["buckets"]] == [
            (str(seeded["k1"].id), "Tariff", 1)
        ]
        assert [
            (b["value"], b["label"], b["count"]) for b in body["story_clusters"]["buckets"]
        ] == [(str(seeded["cluster"].id), seeded["representative"].title, 2)]
        assert not any(group["truncated"] for key, group in body.items() if key != "total")

        # Parity: the facet total is the /search result count for the same criteria.
        search = await client.get("/api/v1/search", params={"q": word, "limit": 100})
        assert len(search.json()["items"]) == body["total"]


async def test_nested_facets_rank_by_articles_not_records(index_name: str) -> None:
    word = f"facetrank{uuid.uuid4().hex[:8]}"
    await _seed(index_name, word)
    async with _client(await _user()) as client:
        # ORG has two records in one article, PERSON one record in each of two articles.
        # Ranking by records would tie 2:2 and pick ORG by key; ranking by articles picks PERSON.
        top = (await _get(client, q=word, limit=1))["entity_types"]
        assert _pairs(top) == [("PERSON", 2)]
        assert top["truncated"] is True


async def test_facets_keep_the_values_of_their_own_active_filter(index_name: str) -> None:
    word = f"facetconj{uuid.uuid4().hex[:8]}"
    seeded = await _seed(index_name, word)
    async with _client(await _user()) as client:
        body = await _get(client, q=word, entity_id=str(seeded["e1"].id))
        assert body["total"] == 2  # A2, A3
        # Conjunctive: the entity filter narrows articles, the facet still lists all their values.
        assert _pairs(body["entity_types"]) == [("PERSON", 2), ("MISC", 1)]
