import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_factory
from app.feeds.models import FeedArticle
from app.search.models import (
    ArticleSearchState,
    SearchDelivery,
    SearchIndexTarget,
    SourceSearchRefresh,
)


def new_claim_token() -> str:
    return uuid.uuid4().hex


def next_retry_at(now: datetime, attempt: int) -> datetime:
    return now + timedelta(seconds=min(3600, 5 * (2 ** max(0, attempt - 1))))


async def claim_delivery(
    db: AsyncSession, delivery_id: uuid.UUID, lease_seconds: int
) -> tuple[SearchDelivery, str] | None:
    now = datetime.now(UTC)
    delivery = await db.scalar(
        select(SearchDelivery).where(SearchDelivery.id == delivery_id).with_for_update()
    )
    if (
        delivery is None
        or delivery.status not in ("queued", "retrying", "running")
        or delivery.next_attempt_at > now
        or (delivery.claim_expires_at is not None and delivery.claim_expires_at > now)
    ):
        return None
    token = new_claim_token()
    delivery.claim_token = token
    delivery.claim_expires_at = now + timedelta(seconds=lease_seconds)
    delivery.status = "running"
    delivery.attempt_count += 1
    await db.commit()
    return delivery, token


def delivery_due(now: datetime):  # type: ignore[no-untyped-def]
    return (
        SearchDelivery.status.in_(("queued", "retrying", "running"))
        & (SearchDelivery.next_attempt_at <= now)
        & or_(
            SearchDelivery.claim_expires_at.is_(None),
            SearchDelivery.claim_expires_at <= now,
        )
    )


async def request_indexing(db: AsyncSession, article_id: uuid.UUID) -> int:
    state = await db.get(ArticleSearchState, article_id, with_for_update=True)
    if state is None:
        state = ArticleSearchState(article_id=article_id, requested_revision=1)
        db.add(state)
    else:
        state.requested_revision += 1
    await db.flush()
    for target in (await db.scalars(select(SearchIndexTarget))).all():
        delivery = await db.scalar(
            select(SearchDelivery)
            .where(SearchDelivery.article_id == article_id, SearchDelivery.target_id == target.id)
            .with_for_update()
        )
        if delivery is None:
            db.add(
                SearchDelivery(
                    article_id=article_id,
                    target_id=target.id,
                    requested_revision=state.requested_revision,
                )
            )
        else:
            delivery.requested_revision = state.requested_revision
            delivery.status = "queued"
            delivery.next_attempt_at = datetime.now(UTC)
            delivery.error_category = None
            delivery.error_message = None
    return state.requested_revision


async def request_source_refresh(db: AsyncSession, feed_id: uuid.UUID) -> None:
    active = await db.scalar(
        select(SourceSearchRefresh).where(
            SourceSearchRefresh.feed_id == feed_id,
            SourceSearchRefresh.status.in_(("queued", "running", "retrying")),
        )
    )
    if active is None:
        db.add(SourceSearchRefresh(feed_id=feed_id))


async def process_source_refresh(refresh_id: uuid.UUID, *, batch_size: int = 500) -> int:
    async with session_factory() as db, db.begin():
        refresh = await db.scalar(
            select(SourceSearchRefresh)
            .where(SourceSearchRefresh.id == refresh_id)
            .with_for_update()
        )
        if refresh is None or refresh.status == "succeeded":
            return 0
        query = (
            select(FeedArticle.article_id)
            .where(FeedArticle.feed_id == refresh.feed_id)
            .order_by(FeedArticle.article_id)
            .limit(batch_size)
        )
        if refresh.article_cursor:
            query = query.where(FeedArticle.article_id > refresh.article_cursor)
        article_ids = list((await db.scalars(query)).all())
        refresh.status = "running"
        refresh.attempt_count += 1
        for article_id in article_ids:
            await request_indexing(db, article_id)
        if article_ids:
            refresh.article_cursor = article_ids[-1]
        if len(article_ids) < batch_size:
            refresh.status = "succeeded"
            refresh.claim_token = None
            refresh.claim_expires_at = None
        return len(article_ids)
