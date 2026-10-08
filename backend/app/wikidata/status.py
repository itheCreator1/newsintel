"""How much we ask of Wikidata, and whether we may: for Operations and `wikidata status`."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.wikidata.client import contact_ok
from app.wikidata.models import (
    EntityExternalId,
    WikidataCandidate,
    WikidataRequestCount,
    WikidataRun,
    WikidataThrottle,
)
from app.wikidata.runs import due_refresh_roots

# Days of request counts shown.
COUNT_DAYS = 7
RECENT_RUNS = 10


@dataclass(frozen=True)
class ThrottleView:
    state: Literal["open", "paused", "budget_spent"]
    paused_until: datetime | None
    pause_reason: str | None
    requests_today: int
    daily_budget: int
    next_request_at: datetime | None


@dataclass(frozen=True)
class CountView:
    day: date
    kind: str
    outcome: str
    count: int
    average_ms: int


@dataclass(frozen=True)
class StatusView:
    enabled: bool
    reason: str | None
    throttle: ThrottleView
    counts: list[CountView]
    links: int
    open_candidates: int
    due_refresh: int
    runs: list[WikidataRun]
    last_refresh: WikidataRun | None


def disabled_reason(settings: Settings) -> str | None:
    if not settings.wikidata_enabled:
        return "Wikidata is switched off (NEWSINTEL_WIKIDATA_ENABLED=false)"
    if not contact_ok(settings.wikidata_contact):
        return (
            "Set NEWSINTEL_WIKIDATA_CONTACT to an email or URL: Wikimedia's User-Agent policy"
            " requires one"
        )
    return None


def _throttle(row: WikidataThrottle | None, settings: Settings, now: datetime) -> ThrottleView:
    budget = settings.wikidata_daily_request_budget
    if row is None:
        return ThrottleView("open", None, None, 0, budget, None)
    today = row.requests_today if row.day == now.date() else 0
    paused = row.paused_until is not None and row.paused_until > now
    return ThrottleView(
        "paused" if paused else "budget_spent" if today >= budget else "open",
        row.paused_until if paused else None,
        row.pause_reason if paused else None,
        today,
        budget,
        row.next_request_at,
    )


async def wikidata_status(
    db: AsyncSession, settings: Settings, now: datetime | None = None
) -> StatusView:
    now = now or datetime.now(UTC)
    reason = disabled_reason(settings)
    counts = [
        CountView(row.day, row.kind, row.outcome, row.count, row.total_ms // max(row.count, 1))
        for row in await db.scalars(
            select(WikidataRequestCount)
            .where(WikidataRequestCount.day >= now.date() - timedelta(days=COUNT_DAYS - 1))
            .order_by(
                WikidataRequestCount.day.desc(),
                WikidataRequestCount.kind,
                WikidataRequestCount.outcome,
            )
        )
    ]
    links = await db.scalar(
        select(func.count())
        .select_from(EntityExternalId)
        .where(EntityExternalId.scheme == "wikidata")
    )
    linked = select(EntityExternalId.entity_id).where(EntityExternalId.scheme == "wikidata")
    open_candidates = await db.scalar(
        select(func.count(func.distinct(WikidataCandidate.entity_id))).where(
            WikidataCandidate.dismissed.is_(False), WikidataCandidate.entity_id.not_in(linked)
        )
    )
    due = await due_refresh_roots(db, settings, after=None, limit=1_000_000, force=False)
    runs = list(
        await db.scalars(
            select(WikidataRun).order_by(WikidataRun.created_at.desc()).limit(RECENT_RUNS)
        )
    )
    last_refresh = await db.scalar(
        select(WikidataRun)
        .where(
            WikidataRun.kind == "refresh",
            WikidataRun.entity_id.is_(None),
            WikidataRun.status.in_(("finished", "failed")),
        )
        .order_by(WikidataRun.finished_at.desc().nulls_last())
        .limit(1)
    )
    return StatusView(
        enabled=reason is None,
        reason=reason,
        throttle=_throttle(await db.get(WikidataThrottle, 1), settings, now),
        counts=counts,
        links=int(links or 0),
        open_candidates=int(open_candidates or 0),
        due_refresh=len(due),
        runs=runs,
        last_refresh=last_refresh,
    )
