"""Every search delivery ends succeeded or failed; none is left running or retried for ever
(fix plan Δ2β)."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.articles.models import Article
from app.core.config import get_settings
from app.db.session import session_factory
from app.search import indexing
from app.search.documents import SCHEMA_VERSION
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable
from app.search.models import SearchDelivery, SearchIndexTarget
from app.search.service import claim_delivery

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _delivery() -> uuid.UUID:
    """A queued delivery to an index no other test writes to (a retained one is never fed)."""
    async with session_factory() as db, db.begin():
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Harbour workers agree a new contract",
            normalized_title_hash=uuid.uuid4().hex,
        )
        target = SearchIndexTarget(
            index_name=f"articles-delivery-{uuid.uuid4().hex}",
            schema_version=SCHEMA_VERSION,
            role="retained",
        )
        db.add_all([article, target])
        await db.flush()
        delivery = SearchDelivery(article_id=article.id, target_id=target.id, requested_revision=1)
        db.add(delivery)
        await db.flush()
        return delivery.id


async def _attempt(delivery_id: uuid.UUID) -> SearchDelivery:
    async with session_factory() as db:
        claimed = await claim_delivery(db, delivery_id, lease_seconds=60)
    assert claimed is not None
    await indexing.process_delivery(str(delivery_id), claimed[1])
    async with session_factory() as db:
        delivery = await db.get(SearchDelivery, delivery_id)
    assert delivery is not None
    assert (delivery.claim_token, delivery.claim_expires_at) == (None, None)
    return delivery


async def _due_now(delivery_id: uuid.UUID) -> None:
    async with session_factory() as db, db.begin():
        await db.execute(
            update(SearchDelivery)
            .where(SearchDelivery.id == delivery_id)
            .values(next_attempt_at=datetime.now(UTC))
        )


async def test_a_delivery_whose_document_is_gone_ends_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def gone(_delivery_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(indexing, "_load_document", gone)
    delivery = await _attempt(await _delivery())
    assert (delivery.status, delivery.error_category) == ("failed", "document_missing")


async def test_a_delivery_stops_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(_self: object, _batch: object) -> list[object]:
        raise ElasticsearchUnavailable("connection refused")

    monkeypatch.setattr(ElasticsearchAdapter, "bulk", down)
    monkeypatch.setattr(get_settings(), "search_max_attempts", 3)
    delivery_id = await _delivery()
    for attempt in (1, 2):
        delivery = await _attempt(delivery_id)
        assert (delivery.status, delivery.attempt_count) == ("retrying", attempt)
        await _due_now(delivery_id)
    delivery = await _attempt(delivery_id)
    assert (delivery.status, delivery.attempt_count) == ("failed", 3)
    assert delivery.error_category == "elasticsearch_transient"


async def test_an_unexpected_error_is_recorded_not_left_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_delivery_id: uuid.UUID) -> None:
        raise RuntimeError("the document could not be built")

    monkeypatch.setattr(indexing, "_load_document", broken)
    delivery = await _attempt(await _delivery())
    assert (delivery.status, delivery.error_category) == ("retrying", "indexing_error")
    assert delivery.error_message == "the document could not be built"
