import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

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


def _encode_cursor(item: Monitor) -> str:
    return base64.urlsafe_b64encode(json.dumps([item.name, str(item.id)]).encode()).decode()


def _decode_cursor(value: str) -> tuple[str, uuid.UUID]:
    try:
        name, item_id = json.loads(base64.urlsafe_b64decode(value.encode()))
        return str(name), uuid.UUID(item_id)
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
    db: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[Monitor], str | None]:
    query = select(Monitor).where(Monitor.user_id == user_id).order_by(Monitor.name, Monitor.id)
    if cursor:
        name, item_id = _decode_cursor(cursor)
        query = query.where(
            or_(Monitor.name > name, and_(Monitor.name == name, Monitor.id > item_id))
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = _encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    return rows[:limit], next_cursor


async def get_monitor(
    db: AsyncSession, user_id: uuid.UUID, monitor_id: uuid.UUID
) -> Monitor | None:
    item: Monitor | None = await db.scalar(
        select(Monitor).where(Monitor.id == monitor_id, Monitor.user_id == user_id)
    )
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
