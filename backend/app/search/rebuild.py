import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article
from app.search.documents import ARTICLE_INDEX_SETTINGS
from app.search.elasticsearch import ElasticsearchAdapter
from app.search.models import (
    ArticleSearchState,
    SearchDelivery,
    SearchIndexTarget,
    SearchRebuild,
    SourceSearchRefresh,
)
from app.search.service import new_claim_token

SCHEMA_VERSION = 1
ALIAS = "articles-current"


def new_index_name(schema_version: int) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%S%fZ").lower()
    return f"articles-v{schema_version}-{stamp}-{uuid.uuid4().hex[:8]}"


async def create_rebuild() -> uuid.UUID:
    index_name = new_index_name(SCHEMA_VERSION)
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    await adapter.create_index(index_name, ARTICLE_INDEX_SETTINGS)
    async with session_factory() as db, db.begin():
        active = await db.scalar(
            select(SearchRebuild.id).where(SearchRebuild.active_key == "active")
        )
        if active is not None:
            raise ValueError(f"rebuild {active} is already active")
        target = SearchIndexTarget(
            index_name=index_name, schema_version=SCHEMA_VERSION, role="replacement"
        )
        db.add(target)
        await db.flush()
        rebuild = SearchRebuild(target_id=target.id)
        db.add(rebuild)
        await db.flush()
        return rebuild.id


async def scan_rebuild(rebuild_id: uuid.UUID, *, batch_size: int = 500) -> int:
    async with session_factory() as db, db.begin():
        rebuild = await db.scalar(
            select(SearchRebuild).where(SearchRebuild.id == rebuild_id).with_for_update()
        )
        if rebuild is None:
            raise LookupError("search rebuild not found")
        if rebuild.status != "scanning":
            return 0
        token = new_claim_token()
        rebuild.coordinator_token = token
        rebuild.coordinator_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        query = select(Article.id).order_by(Article.id).limit(batch_size)
        if rebuild.scan_cursor:
            query = query.where(Article.id > rebuild.scan_cursor)
        article_ids = list((await db.scalars(query)).all())
        for article_id in article_ids:
            state = await db.get(ArticleSearchState, article_id, with_for_update=True)
            if state is None:
                state = ArticleSearchState(article_id=article_id, requested_revision=1)
                db.add(state)
                await db.flush()
            delivery = await db.scalar(
                select(SearchDelivery).where(
                    SearchDelivery.article_id == article_id,
                    SearchDelivery.target_id == rebuild.target_id,
                )
            )
            if delivery is None:
                db.add(
                    SearchDelivery(
                        article_id=article_id,
                        target_id=rebuild.target_id,
                        requested_revision=state.requested_revision,
                    )
                )
        if article_ids:
            rebuild.scan_cursor = article_ids[-1]
            rebuild.scanned_count += len(article_ids)
        if len(article_ids) < batch_size:
            rebuild.status = "catching_up"
        rebuild.coordinator_token = None
        rebuild.coordinator_expires_at = None
        return len(article_ids)


async def try_cutover(rebuild_id: uuid.UUID) -> bool:
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    async with session_factory() as db, db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(728341904)"))
        rebuild = await db.scalar(
            select(SearchRebuild).where(SearchRebuild.id == rebuild_id).with_for_update()
        )
        if rebuild is None:
            raise LookupError("search rebuild not found")
        target = await db.get(SearchIndexTarget, rebuild.target_id)
        if target is None:
            raise LookupError("search target not found")
        if target.index_name in await adapter.alias_indices(ALIAS) and rebuild.cutover_intent_at:
            rebuild.status = "completed"
            rebuild.active_key = None
            rebuild.completed_at = datetime.now(UTC)
            target.role = "current"
            return True
        outstanding = await db.scalar(
            select(func.count())
            .select_from(SearchDelivery)
            .where(
                SearchDelivery.target_id == target.id,
                (SearchDelivery.indexed_revision < SearchDelivery.requested_revision)
                | (SearchDelivery.status == "failed"),
            )
        )
        refreshes = await db.scalar(
            select(func.count())
            .select_from(SourceSearchRefresh)
            .where(SourceSearchRefresh.status != "succeeded")
        )
        if rebuild.status == "scanning" or outstanding or refreshes:
            return False
        rebuild.cutover_intent_at = datetime.now(UTC)
        await db.flush()
        previous = await adapter.alias_indices(ALIAS)
        await adapter.refresh(target.index_name)
        await adapter.switch_alias(ALIAS, target.index_name, previous)
        await db.execute(
            text("UPDATE search_index_targets SET role = 'retained' WHERE role = 'current'")
        )
        target.role = "current"
        rebuild.status = "completed"
        rebuild.active_key = None
        rebuild.completed_at = datetime.now(UTC)
        return True


async def rebuild_status() -> list[dict[str, object]]:
    async with session_factory() as db:
        rows = (
            await db.execute(select(SearchRebuild, SearchIndexTarget).join(SearchIndexTarget))
        ).all()
        return [
            {
                "id": str(rebuild.id),
                "status": rebuild.status,
                "index": target.index_name,
                "scanned": rebuild.scanned_count,
            }
            for rebuild, target in rows
        ]
