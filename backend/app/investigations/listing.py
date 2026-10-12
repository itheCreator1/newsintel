"""Saved searches and monitors as one list: a saved search watched in place is one row."""

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigations.models import SavedSearch
from app.investigations.schemas import STATE_VERSION, SavedSearchResponse
from app.investigations.service import saved_search_response
from app.monitors.models import Monitor
from app.monitors.schemas import MonitorResponse
from app.monitors.service import monitor_response

InvestigationOrder = Literal["name", "activity"]


class Investigation(BaseModel):
    # The saved search's id when there is one, else the monitor's.
    id: uuid.UUID
    name: str
    saved_search: SavedSearchResponse | None
    monitor: MonitorResponse | None


class InvestigationPage(BaseModel):
    items: list[Investigation]
    next_cursor: str | None


class InvalidInvestigationCursor(ValueError):
    pass


class UnwatchableSavedSearch(ValueError):
    pass


class WatchNameTaken(Exception):
    pass


def _encode(key: str | None, item_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps([key, str(item_id)]).encode()).decode()


def _decode(value: str) -> tuple[str | None, uuid.UUID]:
    try:
        key, item_id = json.loads(base64.urlsafe_b64decode(value.encode()))
        if key is not None and not isinstance(key, str):
            raise TypeError(key)
        return key, uuid.UUID(item_id)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidInvestigationCursor("Invalid investigation cursor") from exc


async def list_investigations(
    db: AsyncSession,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int,
    order: InvestigationOrder = "name",
) -> InvestigationPage:
    saved = (
        select(
            literal("saved").label("source"),
            SavedSearch.id.label("id"),
            SavedSearch.name.label("name"),
            Monitor.latest_match_at.label("latest"),
        )
        .outerjoin(Monitor, Monitor.saved_search_id == SavedSearch.id)
        .where(SavedSearch.user_id == user_id)
    )
    watched = select(
        literal("monitor").label("source"),
        Monitor.id.label("id"),
        Monitor.name.label("name"),
        Monitor.latest_match_at.label("latest"),
    ).where(Monitor.user_id == user_id, Monitor.saved_search_id.is_(None))
    rows = union_all(saved, watched).subquery()
    query = select(rows.c.source, rows.c.id, rows.c.name, rows.c.latest)
    if order == "name":
        query = query.order_by(rows.c.name, rows.c.id)
        if cursor:
            name, item_id = _decode(cursor)
            query = query.where(
                or_(rows.c.name > name, and_(rows.c.name == name, rows.c.id > item_id))
            )
    else:
        query = query.order_by(rows.c.latest.desc().nulls_last(), rows.c.id)
        if cursor:
            key, item_id = _decode(cursor)
            if key is None:
                query = query.where(rows.c.latest.is_(None), rows.c.id > item_id)
            else:
                latest = datetime.fromisoformat(key)
                query = query.where(
                    or_(
                        rows.c.latest < latest,
                        and_(rows.c.latest == latest, rows.c.id > item_id),
                        rows.c.latest.is_(None),
                    )
                )
    found = list((await db.execute(query.limit(limit + 1))).all())
    page = found[:limit]
    next_cursor = None
    if len(found) > limit:
        last = page[-1]
        key = last.name if order == "name" else (last.latest.isoformat() if last.latest else None)
        next_cursor = _encode(key, last.id)

    saved_ids = [row.id for row in page if row.source == "saved"]
    monitor_ids = [row.id for row in page if row.source == "monitor"]
    searches = {
        item.id: item
        for item in await db.scalars(select(SavedSearch).where(SavedSearch.id.in_(saved_ids)))
    }
    monitors = list(
        await db.scalars(
            select(Monitor).where(
                or_(Monitor.id.in_(monitor_ids), Monitor.saved_search_id.in_(saved_ids))
            )
        )
    )
    by_id = {item.id: item for item in monitors}
    by_search = {item.saved_search_id: item for item in monitors if item.saved_search_id}
    items = []
    for row in page:
        search = searches.get(row.id) if row.source == "saved" else None
        monitor = by_search.get(row.id) if row.source == "saved" else by_id.get(row.id)
        items.append(
            Investigation(
                id=row.id,
                name=row.name,
                saved_search=saved_search_response(search) if search else None,
                monitor=monitor_response(monitor) if monitor else None,
            )
        )
    return InvestigationPage(items=items, next_cursor=next_cursor)


async def watch_saved_search(db: AsyncSession, item: SavedSearch) -> Monitor:
    """The monitor watching this saved search, created or resumed."""
    monitor: Monitor | None = await db.scalar(
        select(Monitor).where(Monitor.saved_search_id == item.id).with_for_update()
    )
    if monitor is None:
        if saved_search_response(item).state is None or item.state_version != STATE_VERSION:
            raise UnwatchableSavedSearch("This saved search can no longer be opened")
        taken = await db.scalar(
            select(Monitor.name).where(
                Monitor.user_id == item.user_id, func.lower(Monitor.name) == item.name.lower()
            )
        )
        if taken is not None:
            raise WatchNameTaken(
                f"The monitor “{taken}” already has this name. Rename one of them first."
            )
        monitor = Monitor(
            user_id=item.user_id,
            name=item.name,
            kind="search",
            state_version=item.state_version,
            state=item.state,
            saved_search_id=item.id,
        )
        db.add(monitor)
    elif not monitor.enabled:
        monitor.enabled = True
        monitor.next_evaluation_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise WatchNameTaken("A monitor with this name already exists") from exc
    await db.refresh(monitor)
    return monitor
