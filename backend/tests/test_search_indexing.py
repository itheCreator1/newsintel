import json
import uuid
from datetime import UTC, datetime

import pytest

from app.search.documents import ArticleDocument, ProvenanceDocument
from app.search.elasticsearch import BulkDocument, ElasticsearchAdapter, OversizedDocument
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
