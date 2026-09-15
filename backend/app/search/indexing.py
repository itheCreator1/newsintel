import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.clustering.models import StoryCluster, StoryClusterMember
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article, FeedArticle
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleKeyword,
    ArticleLanguageAnnotation,
    Entity,
    Keyword,
)
from app.search.documents import (
    ArticleDocument,
    EntityDocument,
    KeywordDocument,
    ProvenanceDocument,
)
from app.search.elasticsearch import (
    BulkDocument,
    ElasticsearchAdapter,
    ElasticsearchUnavailable,
    OversizedDocument,
)
from app.search.models import SearchDelivery, SearchIndexTarget
from app.search.service import next_retry_at

Outcome = Literal["success", "duplicate", "transient", "permanent"]


def result_outcome(status: int) -> Outcome:
    if 200 <= status < 300:
        return "success"
    if status == 409:
        return "duplicate"
    if status == 429 or status >= 500:
        return "transient"
    return "permanent"


async def _load_document(
    delivery_id: uuid.UUID,
) -> tuple[SearchDelivery, str, int, ArticleDocument] | None:
    async with session_factory() as db, db.begin():
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        delivery = await db.get(SearchDelivery, delivery_id)
        if delivery is None:
            return None
        target = await db.get(SearchIndexTarget, delivery.target_id)
        article = await db.scalar(
            select(Article)
            .where(Article.id == delivery.article_id)
            .options(
                selectinload(Article.discoveries).selectinload(FeedArticle.feed),
                selectinload(Article.content),
                selectinload(Article.processing_jobs),
            )
        )
        if target is None or article is None:
            return None
        language = await db.scalar(
            select(ArticleLanguageAnnotation).where(
                ArticleLanguageAnnotation.article_id == article.id,
                ArticleLanguageAnnotation.is_current.is_(True),
            )
        )
        entity_rows = (
            await db.execute(
                select(ArticleEntity, Entity)
                .join(Entity)
                .where(
                    ArticleEntity.article_id == article.id,
                    ArticleEntity.is_current.is_(True),
                )
            )
        ).all()
        keyword_rows = (
            await db.execute(
                select(ArticleKeyword, Keyword)
                .join(Keyword)
                .where(
                    ArticleKeyword.article_id == article.id,
                    ArticleKeyword.is_current.is_(True),
                )
            )
        ).all()
        country_rows = list(
            (
                await db.scalars(
                    select(ArticleCountryAnnotation).where(
                        ArticleCountryAnnotation.article_id == article.id,
                        ArticleCountryAnnotation.is_current.is_(True),
                    )
                )
            ).all()
        )
        latest_job = max(article.processing_jobs, key=lambda job: job.created_at, default=None)
        membership = await db.scalar(
            select(StoryClusterMember).where(StoryClusterMember.article_id == article.id)
        )
        cluster = (
            await db.get(StoryCluster, membership.cluster_id) if membership is not None else None
        )
        document = ArticleDocument(
            article_id=article.id,
            title=article.title,
            descriptions=[item.description for item in article.discoveries if item.description],
            body=article.content.text if article.content else None,
            published_at=article.published_at,
            first_discovered_at=article.first_discovered_at,
            content_available=article.content is not None,
            processing_status=latest_job.status if latest_job else None,
            provenance=[
                ProvenanceDocument(item.feed.id, item.feed.name, item.feed.source_country)
                for item in article.discoveries
            ],
            detected_language=language.language if language else None,
            entities=[
                EntityDocument(
                    value.id,
                    value.entity_type,
                    value.display_text,
                    value.normalized_text,
                )
                for _, value in entity_rows
            ],
            keywords=[
                KeywordDocument(value.id, value.kind, value.display_text, value.normalized_text)
                for _, value in keyword_rows
            ],
            primary_story_country=next(
                (value.country_code for value in country_rows if value.role == "primary"), None
            ),
            mentioned_countries=[
                value.country_code for value in country_rows if value.role == "mentioned"
            ],
            story_cluster_id=cluster.id if cluster is not None else None,
            cluster_source_count=cluster.source_count if cluster is not None else None,
        )
        db.expunge(delivery)
        return delivery, target.index_name, target.schema_version, document


async def _acknowledge(
    delivery_id: uuid.UUID,
    token: str,
    revision: int,
    outcome: Outcome,
    error: str | None = None,
) -> None:
    async with session_factory() as db, db.begin():
        delivery = await db.scalar(
            select(SearchDelivery).where(SearchDelivery.id == delivery_id).with_for_update()
        )
        if (
            delivery is None
            or delivery.claim_token != token
            or delivery.requested_revision != revision
        ):
            return
        delivery.claim_token = None
        delivery.claim_expires_at = None
        if outcome in ("success", "duplicate"):
            delivery.indexed_revision = revision
            delivery.status = "succeeded"
            delivery.error_category = None
            delivery.error_message = None
        elif outcome == "transient":
            delivery.status = "retrying"
            delivery.next_attempt_at = next_retry_at(datetime.now(UTC), delivery.attempt_count)
            delivery.error_category = "elasticsearch_transient"
            delivery.error_message = (error or "Elasticsearch temporarily unavailable")[:1000]
        else:
            delivery.status = "failed"
            delivery.error_category = "document"
            delivery.error_message = (error or "Elasticsearch rejected document")[:1000]


async def process_delivery(delivery_id: str, claim_token: str) -> None:
    parsed_id = uuid.UUID(delivery_id)
    loaded = await _load_document(parsed_id)
    if loaded is None:
        return
    delivery, index_name, schema_version, document = loaded
    if delivery.claim_token != claim_token:
        return
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    try:
        batches = adapter.build_batches(
            index_name,
            [
                BulkDocument(
                    str(document.article_id),
                    delivery.requested_revision,
                    document.to_index_payload(schema_version=schema_version),
                )
            ],
        )
        result = (await adapter.bulk(batches[0]))[0]
        await _acknowledge(
            parsed_id,
            claim_token,
            delivery.requested_revision,
            result_outcome(result.status),
            result.error,
        )
    except OversizedDocument as exc:
        await _acknowledge(
            parsed_id, claim_token, delivery.requested_revision, "permanent", str(exc)
        )
    except ElasticsearchUnavailable as exc:
        await _acknowledge(
            parsed_id, claim_token, delivery.requested_revision, "transient", str(exc)
        )
