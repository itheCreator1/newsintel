import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article
from app.investigations.schemas import InvestigationState
from app.monitors.models import Monitor
from app.monitors.service import parse_state
from app.search.criteria import build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

log = structlog.get_logger()


class SearchAdapter(Protocol):
    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Snapshot:
    """What an evaluator needs from the monitor row; the row itself stays with the publisher."""

    kind: str
    state: InvestigationState
    eval_cursor_at: datetime | None
    viewed_cursor_at: datetime | None


@dataclass(frozen=True)
class Window:
    """The outcome of one evaluation. `None` means leave that column as it is."""

    eval_cursor_at: datetime
    viewed_cursor_at: datetime | None = None
    unseen_articles: int | None = None
    unseen_clusters: int | None = None
    latest_match_at: datetime | None = None
    latest_match_article_id: uuid.UUID | None = None


Evaluator = Callable[[AsyncSession, SearchAdapter, Snapshot, datetime], Awaitable[Window]]


class StaleClaim(Exception):
    """The lease, the target or the viewed boundary changed while the evaluation ran."""


class InvalidState(Exception):
    pass


def criteria_params(state: InvestigationState) -> dict[str, Any]:
    return state.model_dump(exclude={"sort", "interval"})


def window_body(
    query: dict[str, Any],
    after: datetime | None,
    upto: datetime,
    *,
    clusters: bool,
    size: int = 1,
) -> dict[str, Any]:
    """Count (and at most one hit) for matches first discovered in `(after, upto]`."""
    bounds = {"lte": upto.isoformat()}
    if after is not None:
        bounds["gt"] = after.isoformat()
    body: dict[str, Any] = {
        "size": size,
        "track_total_hits": True,
        "query": {"bool": {"filter": [query, {"range": {"first_discovered_at": bounds}}]}},
        "sort": [{"first_discovered_at": "desc"}, {"article_id": "desc"}],
        "_source": ["article_id", "first_discovered_at"],
    }
    if clusters:
        body["aggs"] = {
            "clusters": {"cardinality": {"field": "story_cluster_id", "precision_threshold": 3000}}
        }
    return body


def _latest(response: dict[str, Any]) -> tuple[datetime | None, uuid.UUID | None]:
    hits = response["hits"]["hits"]
    if not hits:
        return None, None
    source = hits[0]["_source"]
    found_at = datetime.fromisoformat(str(source["first_discovered_at"]).replace("Z", "+00:00"))
    return found_at, uuid.UUID(source["article_id"])


async def search_evaluator(
    db: AsyncSession, adapter: SearchAdapter, snapshot: Snapshot, horizon: datetime
) -> Window:
    """Counts by time window; a rerun of the same window always yields the same numbers."""
    cursor = snapshot.eval_cursor_at
    if cursor is not None and horizon <= cursor:
        return Window(eval_cursor_at=cursor)
    criteria = await search_criteria(db, **criteria_params(snapshot.state))
    index_name, schema_version = await current_search_target(db, criteria)
    query = build_query(criteria, schema_version)
    if cursor is None:
        # The archive that exists now is not "unseen"; start counting from here.
        latest_at, latest_id = _latest(
            await adapter.search_index(
                index_name, window_body(query, None, horizon, clusters=False)
            )
        )
        return Window(horizon, horizon, 0, 0, latest_at, latest_id)
    delta = await adapter.search_index(
        index_name, window_body(query, cursor, horizon, clusters=False, size=0)
    )
    if delta["hits"]["total"]["value"] == 0:
        return Window(eval_cursor_at=horizon)
    unseen = await adapter.search_index(
        index_name,
        window_body(query, snapshot.viewed_cursor_at or cursor, horizon, clusters=True),
    )
    latest_at, latest_id = _latest(unseen)
    return Window(
        horizon,
        unseen_articles=unseen["hits"]["total"]["value"],
        unseen_clusters=unseen["aggregations"]["clusters"]["value"],
        latest_match_at=latest_at,
        latest_match_article_id=latest_id,
    )


# Every kind is a search over the same state today; a kind that needs its own query swaps in here.
EVALUATORS: dict[str, Evaluator] = {
    kind: search_evaluator for kind in ("search", "entity", "source", "country", "cluster")
}


def monitor_due(now: datetime):  # type: ignore[no-untyped-def]
    return (
        Monitor.enabled.is_(True)
        & (Monitor.next_evaluation_at <= now)
        & or_(Monitor.claim_expires_at.is_(None), Monitor.claim_expires_at <= now)
    )


async def claim_monitor(db: AsyncSession, monitor_id: uuid.UUID, lease_seconds: int) -> str | None:
    now = datetime.now(UTC)
    item = await db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
    if (
        item is None
        or not item.enabled
        or item.next_evaluation_at > now
        or (item.claim_expires_at is not None and item.claim_expires_at > now)
    ):
        return None
    item.claim_token = uuid.uuid4().hex
    item.claim_expires_at = now + timedelta(seconds=lease_seconds)
    await db.commit()
    return item.claim_token


def _holds_lease(item: Monitor | None, token: str) -> bool:
    # The lease runs on the wall clock even when a caller supplies its own evaluation time.
    return (
        item is not None
        and item.claim_token == token
        and item.claim_expires_at is not None
        and item.claim_expires_at > datetime.now(UTC)
    )


async def _publish(
    monitor_id: uuid.UUID, token: str, snapshot: Snapshot, window: Window, now: datetime
) -> None:
    async with session_factory() as db, db.begin():
        item = await db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
        if item is None or not _holds_lease(item, token):
            raise StaleClaim
        if item.viewed_cursor_at != snapshot.viewed_cursor_at:
            raise StaleClaim
        item.eval_cursor_at = window.eval_cursor_at
        if window.viewed_cursor_at is not None:
            item.viewed_cursor_at = window.viewed_cursor_at
        if window.unseen_articles is not None and window.unseen_clusters is not None:
            item.unseen_article_count = window.unseen_articles
            item.unseen_cluster_count = window.unseen_clusters
        # The index is rebuildable and can name an article PostgreSQL no longer has.
        if (
            window.latest_match_article_id is not None
            and await db.get(Article, window.latest_match_article_id) is not None
        ):
            item.latest_match_at = window.latest_match_at
            item.latest_match_article_id = window.latest_match_article_id
        item.last_evaluated_at = now
        item.next_evaluation_at = now + timedelta(seconds=get_settings().monitor_interval_seconds)
        item.claim_token = item.claim_expires_at = None
        item.error_category = item.error_message = None


def _category(exc: Exception) -> str:
    if isinstance(exc, InvalidState):
        return "invalid_state"
    if isinstance(exc, ElasticsearchUnavailable):
        return "search_unavailable"
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) and exc.detail.get("code"):
        return str(exc.detail["code"])
    return "evaluation_error"


async def _record_failure(monitor_id: uuid.UUID, token: str, exc: Exception, now: datetime) -> None:
    category = _category(exc)
    log.error("monitor_evaluation_failed", monitor_id=str(monitor_id), error_category=category)
    async with session_factory() as db, db.begin():
        item = await db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
        if item is None or item.claim_token != token:
            return
        item.claim_token = item.claim_expires_at = None
        item.error_category = category
        item.error_message = str(exc)[:1000]
        # ponytail: fixed retry delay (the row has no attempt counter); add one for backoff.
        item.next_evaluation_at = now + timedelta(seconds=get_settings().monitor_retry_seconds)


async def process_monitor(
    monitor_id: uuid.UUID,
    token: str,
    adapter: SearchAdapter | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Evaluate one claimed monitor; True only when this call published the result."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    try:
        async with session_factory() as db:
            item = await db.get(Monitor, monitor_id)
            if item is None or not item.enabled or not _holds_lease(item, token):
                return False
            state, problem = parse_state(item)
            if state is None:
                raise InvalidState(problem)
            snapshot = Snapshot(item.kind, state, item.eval_cursor_at, item.viewed_cursor_at)
            # ponytail: the session stays open across the search round trips (bounded by the
            # 30s client timeout); split the load from the evaluation if that ever pinches.
            window = await EVALUATORS[item.kind](
                db,
                adapter or ElasticsearchAdapter(settings.elasticsearch_url),
                snapshot,
                now - timedelta(seconds=settings.monitor_settle_seconds),
            )
        await _publish(monitor_id, token, snapshot, window, now)
    except StaleClaim:
        return False
    except Exception as exc:
        await _record_failure(monitor_id, token, exc, now)
        return False
    return True
