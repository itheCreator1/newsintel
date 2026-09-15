import base64
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.feeds.models import Article, Feed, FeedFetch
from app.feeds.schemas import (
    ArticleContentResponse,
    ArticleDetailResponse,
    ArticleJobSummary,
    ArticleProvenance,
    ArticleResponse,
    ArticleStoryCluster,
    RelatedArticle,
)


def encode_cursor(created: datetime, item_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created.isoformat()}|{item_id}".encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    timestamp, item_id = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
    return datetime.fromisoformat(timestamp), uuid.UUID(item_id)


async def claim_feed(
    db: AsyncSession, feed_id: uuid.UUID, lease_seconds: int = 300
) -> tuple[FeedFetch, bool] | None:
    now = datetime.now(UTC)
    feed = await db.scalar(select(Feed).where(Feed.id == feed_id).with_for_update())
    if feed is None or not feed.enabled or feed.retired_at is not None:
        return None
    if feed.claim_token and feed.claim_expires_at and feed.claim_expires_at > now:
        active = await db.scalar(select(FeedFetch).where(FeedFetch.claim_token == feed.claim_token))
        if active:
            return active, True
    token = uuid.uuid4().hex
    fetch = FeedFetch(feed_id=feed.id, claim_token=token)
    feed.claim_token = token
    feed.claim_expires_at = now + timedelta(seconds=lease_seconds)
    db.add(fetch)
    await db.commit()
    await db.refresh(fetch)
    return fetch, False


def article_response(article: Article) -> ArticleResponse:
    return ArticleResponse(
        id=article.id,
        original_url=article.original_url,
        normalized_url=article.normalized_url,
        title=article.title,
        published_at=article.published_at,
        first_discovered_at=article.first_discovered_at,
        provenance=[
            ArticleProvenance(
                feed_id=item.feed_id,
                feed_name=item.feed.name,
                guid=item.guid,
                title=item.feed_title,
                url=item.feed_url,
                description=item.description,
                discovered_at=item.discovered_at,
            )
            for item in article.discoveries
        ],
    )


def article_detail_response(
    article: Article,
    *,
    clustering_status: str | None = None,
    story_cluster: ArticleStoryCluster | None = None,
    related: list[RelatedArticle] | None = None,
) -> ArticleDetailResponse:
    basic = article_response(article)
    content = article.content
    return ArticleDetailResponse(
        **basic.model_dump(),
        content=(
            ArticleContentResponse(
                text=content.text,
                content_hash=content.content_hash,
                previous_content_hash=content.previous_content_hash,
                change_count=content.change_count,
                extractor_name=content.extractor_name,
                extractor_version=content.extractor_version,
                extracted_at=content.extracted_at,
                last_content_change_at=content.last_content_change_at,
                html_retained=content.html_object_key is not None,
            )
            if content
            else None
        ),
        processing=[
            ArticleJobSummary.model_validate(job, from_attributes=True)
            for job in sorted(
                article.processing_jobs, key=lambda item: item.created_at, reverse=True
            )
        ],
        clustering_status=clustering_status,
        story_cluster=story_cluster,
        related=related or [],
    )
