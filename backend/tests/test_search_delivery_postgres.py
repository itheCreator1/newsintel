import os
import uuid
from datetime import UTC, datetime

import pytest
from event_fixtures import feed
from sqlalchemy import delete, select
from test_phase13b_postgres import _article

from app.db.session import session_factory
from app.search import indexing
from app.search.models import SearchDelivery, SearchIndexTarget
from app.search.service import claim_delivery, delivery_due, request_indexing

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_a_revision_requested_while_indexing_is_due_as_soon_as_the_old_one_finishes() -> None:
    async with session_factory() as db:
        target = None
        if not await db.scalar(
            select(SearchIndexTarget.id).where(SearchIndexTarget.role == "current")
        ):
            target = SearchIndexTarget(
                index_name=f"idx-{uuid.uuid4().hex}", schema_version=1, role="current"
            )
            db.add(target)
        article = await _article(db, await feed(db), datetime.now(UTC))
        await request_indexing(db, article.id)
        await db.commit()
        delivery_id = await db.scalar(
            select(SearchDelivery.id).where(SearchDelivery.article_id == article.id).limit(1)
        )
        assert delivery_id is not None
        claimed = await claim_delivery(db, delivery_id, lease_seconds=300)
        assert claimed is not None
        delivery, token = claimed
        first = delivery.requested_revision
        # The article changes while revision `first` is being indexed.
        await request_indexing(db, article.id)
        await db.commit()

    await indexing._acknowledge(delivery_id, token, first, "success")

    try:
        async with session_factory() as db:
            row = await db.get(SearchDelivery, delivery_id)
            assert row is not None
            assert (row.status, row.indexed_revision, row.claim_expires_at) == ("queued", 0, None)
            due = await db.scalar(
                select(SearchDelivery.id).where(
                    SearchDelivery.id == delivery_id, delivery_due(datetime.now(UTC))
                )
            )
            assert due == delivery_id
    finally:
        async with session_factory() as db, db.begin():
            # The article stays, as in the other gated tests; a target made here must not linger.
            await db.execute(delete(SearchDelivery).where(SearchDelivery.article_id == article.id))
            if target is not None:
                await db.execute(delete(SearchIndexTarget).where(SearchIndexTarget.id == target.id))
