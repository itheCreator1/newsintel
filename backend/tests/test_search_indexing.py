import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.search.documents import (
    SCHEMA_VERSION,
    ArticleDocument,
    EntityDocument,
    KeywordDocument,
    ProvenanceDocument,
)
from app.search.elasticsearch import (
    BulkDocument,
    ElasticsearchAdapter,
    OversizedDocument,
    close_shared_client,
    open_shared_client,
)
from app.search.indexing import result_outcome


def test_article_document_preserves_nested_provenance_and_effective_date() -> None:
    discovered = datetime(2026, 9, 14, 10, tzinfo=UTC)
    document = ArticleDocument(
        article_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        title="A title",
        descriptions=["<p>Plain description</p>"],
        body="Extracted body",
        published_at=None,
        first_discovered_at=discovered,
        content_available=True,
        processing_status="succeeded",
        provenance=[
            ProvenanceDocument(
                source_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
                source_name="Wire",
                source_country="gr",
            )
        ],
    )

    payload = document.to_index_payload()

    assert payload["effective_date"] == "2026-09-14T10:00:00+00:00"
    assert payload["descriptions"] == ["Plain description"]
    assert payload["distinct_source_count"] == 1
    assert payload["provenance"] == [
        {
            "source_id": "22222222-2222-2222-2222-222222222222",
            "source_name": "Wire",
            "source_country": "GR",
        }
    ]


def test_bulk_requests_are_bounded_and_oversized_documents_are_visible() -> None:
    adapter = ElasticsearchAdapter("http://elasticsearch.test", max_documents=2, max_bytes=220)
    documents = [BulkDocument(str(number), number, {"title": "small"}) for number in range(1, 4)]

    batches = adapter.build_batches("articles-v1", documents)

    assert [len(batch.documents) for batch in batches] == [2, 1]
    with pytest.raises(OversizedDocument, match="exceeds"):
        adapter.build_batches("articles-v1", [BulkDocument("large", 1, {"body": "x" * 500})])


def test_bulk_body_uses_external_gte_versioning() -> None:
    adapter = ElasticsearchAdapter("http://elasticsearch.test")
    batch = adapter.build_batches(
        "articles-v1", [BulkDocument("article-id", 7, {"title": "News"})]
    )[0]
    lines = batch.body.decode().splitlines()

    assert json.loads(lines[0]) == {
        "index": {
            "_id": "article-id",
            "_index": "articles-v1",
            "version": 7,
            "version_type": "external_gte",
        }
    }
    assert json.loads(lines[1]) == {"title": "News"}


def test_bulk_results_distinguish_conflicts_transient_and_permanent_failures() -> None:
    assert result_outcome(201) == "success"
    assert result_outcome(409) == "duplicate"
    assert result_outcome(429) == "transient"
    assert result_outcome(503) == "transient"
    assert result_outcome(400) == "permanent"


def test_article_document_serializes_its_annotations() -> None:
    document = ArticleDocument(
        article_id=uuid.uuid4(),
        title="France energy policy",
        descriptions=[],
        body=None,
        published_at=None,
        first_discovered_at=datetime(2026, 9, 14, tzinfo=UTC),
        content_available=False,
        processing_status=None,
        provenance=[],
        detected_language="en",
        entities=[EntityDocument(uuid.uuid4(), "ORG", "European Union", "european union")],
        keywords=[KeywordDocument(uuid.uuid4(), "keyphrase", "energy policy", "energy policy")],
        primary_story_country="FR",
        mentioned_countries=["FR", "DE"],
    )

    payload = document.to_index_payload()
    assert payload["detected_language"] == "en"
    assert payload["entity_text"] == ["European Union"]
    assert payload["keyword_text"] == ["energy policy"]
    assert payload["primary_story_country"] == "FR"
    assert payload["mentioned_countries"] == ["DE", "FR"]
    assert payload["story_cluster_id"] is None


def test_document_lists_authorized_name_and_variants() -> None:
    entity_id = uuid.uuid4()
    document = ArticleDocument(
        article_id=uuid.uuid4(),
        title="Harbour board meets",
        descriptions=[],
        body=None,
        published_at=None,
        first_discovered_at=datetime(2026, 9, 14, tzinfo=UTC),
        content_available=False,
        processing_status=None,
        provenance=[],
        entities=[
            EntityDocument(
                entity_id,
                "ORG",
                "Quill Harbour Board",
                "quill harbour board",
                other_names=("QHB", "Harbour Board of Quill"),
            )
        ],
    )

    payload = document.to_index_payload()
    # Free text finds the article by any name; the nested entity shows the authorized one.
    assert payload["entity_text"] == ["Quill Harbour Board", "QHB", "Harbour Board of Quill"]
    assert payload["entities"] == [
        {
            "id": str(entity_id),
            "type": "ORG",
            "text": "Quill Harbour Board",
            "normalized_text": "quill harbour board",
        }
    ]


def test_article_document_serializes_cluster_membership() -> None:
    cluster_id = uuid.uuid4()
    document = ArticleDocument(
        article_id=uuid.uuid4(),
        title="A clustered story",
        descriptions=[],
        body=None,
        published_at=None,
        first_discovered_at=datetime(2026, 9, 14, tzinfo=UTC),
        content_available=False,
        processing_status=None,
        provenance=[],
        story_cluster_id=cluster_id,
        cluster_source_count=3,
    )

    payload = document.to_index_payload()
    assert payload["story_cluster_id"] == str(cluster_id)
    assert payload["cluster_source_count"] == 3


def test_article_document_serializes_no_cluster_as_null() -> None:
    document = ArticleDocument(
        article_id=uuid.uuid4(),
        title="An unclustered story",
        descriptions=[],
        body=None,
        published_at=None,
        first_discovered_at=datetime(2026, 9, 14, tzinfo=UTC),
        content_available=False,
        processing_status=None,
        provenance=[],
    )

    payload = document.to_index_payload()
    assert payload["story_cluster_id"] is None
    assert payload["cluster_source_count"] is None


@pytest.mark.asyncio
async def test_adapter_reuses_the_shared_client_while_one_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[httpx.AsyncClient] = []
    original = httpx.AsyncClient

    def tracking_client(**kwargs: object) -> httpx.AsyncClient:
        client = original(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
            **kwargs,  # type: ignore[arg-type]
        )
        opened.append(client)
        return client

    monkeypatch.setattr("app.search.elasticsearch.httpx.AsyncClient", tracking_client)
    adapter = ElasticsearchAdapter("http://elasticsearch:9200")

    await adapter.refresh("articles")
    await adapter.refresh("articles")
    assert len(opened) == 2

    open_shared_client("http://elasticsearch:9200/")
    try:
        await adapter.refresh("articles")
        await adapter.refresh("articles")
        assert len(opened) == 3
        assert not opened[2].is_closed
    finally:
        await close_shared_client()
    assert opened[2].is_closed


class _StatusDatabase:
    """The two queries /search/indexing/status runs: delivery counts, then the current index."""

    def __init__(self, schema_version: int | None) -> None:
        self.current = (
            None
            if schema_version is None
            else SimpleNamespace(index_name="articles-test", schema_version=schema_version)
        )

    async def execute(self, _query: object) -> object:
        rows = [("queued", 3), ("failed", 1)]
        return SimpleNamespace(all=lambda: rows)

    async def scalar(self, _query: object) -> object:
        return self.current


@pytest.mark.parametrize(
    ("current", "ready", "rebuild_needed"),
    [(None, False, False), (SCHEMA_VERSION, True, False), (SCHEMA_VERSION - 1, True, True)],
)
@pytest.mark.asyncio
async def test_indexing_status_reports_index_ready_only_with_a_current_index(
    monkeypatch: pytest.MonkeyPatch, current: int | None, ready: bool, rebuild_needed: bool
) -> None:
    from app.search import routes

    async def no_rebuilds() -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(routes, "rebuild_status", no_rebuilds)

    status = await routes.indexing_status(_StatusDatabase(current), None)  # type: ignore[arg-type]

    assert status.index_ready is ready
    # An index an older version built is there, but Search refuses it until it is rebuilt.
    assert status.rebuild_needed is rebuild_needed
    assert (status.queued, status.running, status.retrying, status.failed) == (3, 0, 0, 1)
    assert status.active_rebuild is None


@pytest.mark.asyncio
async def test_indexing_status_reports_the_unfinished_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.search import routes

    async def rebuilds() -> list[dict[str, object]]:
        return [{"status": "completed"}, {"status": "copying"}]

    monkeypatch.setattr(routes, "rebuild_status", rebuilds)

    status = await routes.indexing_status(_StatusDatabase(None), None)  # type: ignore[arg-type]

    assert status.active_rebuild == {"status": "copying"}
    assert status.index_ready is False
