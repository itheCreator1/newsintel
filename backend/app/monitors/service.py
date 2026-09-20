import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigations.schemas import STATE_VERSION, InvestigationState
from app.monitors.models import Monitor
from app.monitors.schemas import (
    MonitorCreate,
    MonitorKind,
    MonitorResponse,
    MonitorUpdate,
    validate_target,
)

# Evaluation history describes one specific query, so it is discarded when the target changes.
_HISTORY_FIELDS = (
    "eval_cursor_at",
    "eval_cursor_article_id",
    "viewed_cursor_at",
    "viewed_cursor_article_id",
    "latest_match_at",
    "latest_match_article_id",
    "last_evaluated_at",
    "claim_token",
    "claim_expires_at",
    "error_category",
    "error_message",
)


class MonitorNameConflict(Exception):
    pass


class InvalidMonitorCursor(ValueError):
    pass


class NotYetEvaluated(Exception):
    pass


class ViewedBeyondEvaluation(ValueError):
    pass


def _encode_cursor(item: Monitor) -> str:
    return base64.urlsafe_b64encode(json.dumps([item.name, str(item.id)]).encode()).decode()


def _decode_cursor(value: str) -> tuple[str, uuid.UUID]:
    try:
        name, item_id = json.loads(base64.urlsafe_b64decode(value.encode()))
        return str(name), uuid.UUID(item_id)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidMonitorCursor("Invalid monitor cursor") from exc


def _encode_activity_cursor(item: Monitor) -> str:
    latest = item.latest_match_at.isoformat() if item.latest_match_at else None
    return base64.urlsafe_b64encode(json.dumps([latest, str(item.id)]).encode()).decode()


def _decode_activity_cursor(value: str) -> tuple[datetime | None, uuid.UUID]:
    try:
        latest, item_id = json.loads(base64.urlsafe_b64decode(value.encode()))
        return (datetime.fromisoformat(latest) if latest else None), uuid.UUID(item_id)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidMonitorCursor("Invalid monitor cursor") from exc


def parse_state(item: Monitor) -> tuple[InvestigationState | None, str | None]:
    """The stored state, or the reason it can no longer be used."""
    if item.state_version != STATE_VERSION:
        return None, f"Monitor format version {item.state_version} is not supported"
    try:
        state = InvestigationState.model_validate(item.state)
        validate_target(cast(MonitorKind, item.kind), state)
    except ValidationError as exc:
        return None, "; ".join(error["msg"] for error in exc.errors())
    except ValueError as exc:
        return None, str(exc)
    return state, None


def monitor_response(item: Monitor) -> MonitorResponse:
    state, problem = parse_state(item)
    return MonitorResponse(
        id=item.id,
        name=item.name,
        kind=cast(MonitorKind, item.kind),
        enabled=item.enabled,
        state_version=item.state_version,
        state=state,
        problem=problem,
        unseen_article_count=item.unseen_article_count,
        unseen_cluster_count=item.unseen_cluster_count,
        evaluated_through=item.eval_cursor_at,
        viewed_through=item.viewed_cursor_at,
        latest_match_at=item.latest_match_at,
        latest_match_article_id=item.latest_match_article_id,
        last_evaluated_at=item.last_evaluated_at,
        next_evaluation_at=item.next_evaluation_at,
        error_category=item.error_category,
        error_message=item.error_message,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def list_monitors(
    db: AsyncSession,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int,
    order: Literal["name", "activity"] = "name",
) -> tuple[list[Monitor], str | None]:
    query = select(Monitor).where(Monitor.user_id == user_id)
    if order == "name":
        query = query.order_by(Monitor.name, Monitor.id)
        if cursor:
            name, item_id = _decode_cursor(cursor)
            query = query.where(
                or_(Monitor.name > name, and_(Monitor.name == name, Monitor.id > item_id))
            )
        encode = _encode_cursor
    else:
        query = query.order_by(Monitor.latest_match_at.desc().nulls_last(), Monitor.id)
        if cursor:
            latest, item_id = _decode_activity_cursor(cursor)
            if latest is None:
                query = query.where(Monitor.latest_match_at.is_(None), Monitor.id > item_id)
            else:
                query = query.where(
                    or_(
                        Monitor.latest_match_at < latest,
                        and_(Monitor.latest_match_at == latest, Monitor.id > item_id),
                        Monitor.latest_match_at.is_(None),
                    )
                )
        encode = _encode_activity_cursor
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = encode(rows[limit - 1]) if len(rows) > limit else None
    return rows[:limit], next_cursor


async def get_monitor(
    db: AsyncSession, user_id: uuid.UUID, monitor_id: uuid.UUID, *, lock: bool = False
) -> Monitor | None:
    query = select(Monitor).where(Monitor.id == monitor_id, Monitor.user_id == user_id)
    item: Monitor | None = await db.scalar(query.with_for_update() if lock else query)
    return item


async def _commit(db: AsyncSession, item: Monitor) -> Monitor:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise MonitorNameConflict("A monitor with this name already exists") from exc
    await db.refresh(item)
    return item


async def create_monitor(db: AsyncSession, user_id: uuid.UUID, payload: MonitorCreate) -> Monitor:
    item = Monitor(
        user_id=user_id,
        name=payload.name,
        kind=payload.kind,
        state_version=STATE_VERSION,
        state=payload.state.model_dump(mode="json"),
    )
    db.add(item)
    return await _commit(db, item)


async def update_monitor(db: AsyncSession, item: Monitor, payload: MonitorUpdate) -> Monitor:
    if payload.name is not None:
        item.name = payload.name
    if payload.enabled is not None:
        if payload.enabled and not item.enabled:
            item.next_evaluation_at = datetime.now(UTC)
        item.enabled = payload.enabled
    if payload.kind is not None and payload.state is not None:
        state: dict[str, Any] = payload.state.model_dump(mode="json")
        if (payload.kind, STATE_VERSION, state) != (item.kind, item.state_version, item.state):
            item.kind, item.state_version, item.state = payload.kind, STATE_VERSION, state
            for field in _HISTORY_FIELDS:
                setattr(item, field, None)
            item.unseen_article_count = item.unseen_cluster_count = 0
            item.next_evaluation_at = datetime.now(UTC)
    return await _commit(db, item)


async def mark_viewed(db: AsyncSession, item: Monitor, through: datetime) -> Monitor:
    """Record that the analyst saw everything up to `through`; `item` must be loaded `lock=True`.

    A boundary behind the evaluation cursor rewinds that cursor to it: evaluation is derived from
    the cursors, so the next run recounts what lies after `through` instead of losing it.
    """
    if item.eval_cursor_at is None:
        raise NotYetEvaluated("This monitor has not been evaluated yet")
    if through > item.eval_cursor_at:
        raise ViewedBeyondEvaluation("through is later than the last evaluation")
    if item.viewed_cursor_at is not None and through <= item.viewed_cursor_at:
        return item
    if through < item.eval_cursor_at:
        item.eval_cursor_at = through
        item.next_evaluation_at = datetime.now(UTC)
    item.viewed_cursor_at = through
    item.unseen_article_count = item.unseen_cluster_count = 0
    # An evaluation still running was counted against the old boundary; it must not publish.
    item.claim_token = item.claim_expires_at = None
    return await _commit(db, item)
