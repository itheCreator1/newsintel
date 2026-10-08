"""Wikidata work that waits its turn: a daily sweep for candidates and the "Find" button.

Nothing that talks to Wikidata runs in a request (CONTRIBUTING §8): the API queues a run, the
scheduler hands one run at a time to a worker, and the worker does a batch of it. Runs go in the
order the design sets: the user's buttons, then refreshes, then the background sweep. While the
shared throttle is paused or the day's budget is spent, nothing is handed out at all.
"""

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import session_factory
from app.nlp.models import ArticleEntity, Entity
from app.wikidata.candidates import find_candidates
from app.wikidata.client import WikidataClient, contact_ok
from app.wikidata.errors import (
    WikidataBudgetSpent,
    WikidataDisabled,
    WikidataError,
    WikidataPaused,
    WikidataUnavailable,
)
from app.wikidata.links import LinkError
from app.wikidata.models import EntityExternalId, WikidataRun, WikidataThrottle
from app.wikidata.throttle import PostgresThrottle, ThrottleState, wait_seconds
from app.wikidata.types import TYPE_ROOTS

log = structlog.get_logger()

# Roots a sweep searches between saving its place.
SWEEP_BATCH = 10
# The run kinds a worker can do; refreshes come with the monthly refresh.
WORKED = ("candidates",)
# Serializes claiming between the scheduler and anything else that claims.
CLAIM_LOCK = 0x57494B49
UNFINISHED = ("queued", "running")


def enabled(settings: Settings) -> bool:
    return settings.wikidata_enabled and contact_ok(settings.wikidata_contact)


async def eligible_roots(
    db: AsyncSession, settings: Settings, *, after: uuid.UUID | None, limit: int
) -> list[uuid.UUID]:
    """Roots the sweep searches: unlinked, not ambiguous, of a typed kind, with enough articles."""
    articles = (
        select(func.count(func.distinct(ArticleEntity.article_id)))
        .where(ArticleEntity.entity_id == Entity.id, ArticleEntity.is_current.is_(True))
        .correlate(Entity)
        .scalar_subquery()
    )
    query = select(Entity.id).where(
        Entity.authority_id.is_(None),
        Entity.ambiguous.is_(False),
        Entity.entity_type.in_(list(TYPE_ROOTS)),
        Entity.language.in_(list(settings.wikidata_languages)),
        ~exists().where(
            EntityExternalId.entity_id == Entity.id, EntityExternalId.scheme == "wikidata"
        ),
        articles >= settings.wikidata_candidate_min_articles,
    )
    if after is not None:
        query = query.where(Entity.id > after)
    return list((await db.scalars(query.order_by(Entity.id).limit(limit))).all())


async def request_search(db: AsyncSession, entity_id: uuid.UUID) -> WikidataRun:
    """Queue a candidate search for the entity's root (the button); one at a time per root."""
    entity = await db.get(Entity, entity_id)
    if entity is None:
        raise LinkError(404, "Entity not found")
    root_id = entity.authority_id or entity.id
    linked = await db.scalar(
        select(
            exists().where(
                EntityExternalId.entity_id == root_id, EntityExternalId.scheme == "wikidata"
            )
        )
    )
    if linked:
        raise LinkError(409, "This entity is already linked to Wikidata")
    run: WikidataRun | None = await db.scalar(
        select(WikidataRun).where(
            WikidataRun.entity_id == root_id,
            WikidataRun.kind == "candidates",
            WikidataRun.status.in_(UNFINISHED),
        )
    )
    if run is None:
        run = WikidataRun(kind="candidates", entity_id=root_id)
        db.add(run)
        await db.flush()
        await db.refresh(run)
    return run


async def search_pending(db: AsyncSession, root_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    WikidataRun.entity_id == root_id,
                    WikidataRun.kind == "candidates",
                    WikidataRun.status.in_(UNFINISHED),
                )
            )
        )
    )


async def ensure_sweep(db: AsyncSession, settings: Settings) -> WikidataRun | None:
    """Queue the day's candidate sweep, unless one is unfinished or finished within the day."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_LOCK})
    sweeps = select(WikidataRun).where(
        WikidataRun.kind == "candidates", WikidataRun.entity_id.is_(None)
    )
    if await db.scalar(sweeps.where(WikidataRun.status.in_(UNFINISHED)).limit(1)) is not None:
        return None
    since = datetime.now(UTC) - timedelta(hours=settings.wikidata_candidate_sweep_hours)
    recent = sweeps.where(WikidataRun.status == "finished", WikidataRun.finished_at >= since)
    if await db.scalar(recent.limit(1)) is not None:
        return None
    run = WikidataRun(kind="candidates")
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def blocked(db: AsyncSession, settings: Settings, now: datetime) -> str | None:
    """Why no request may go out now (a pause, the spent budget), as the throttle would say."""
    row = await db.get(WikidataThrottle, 1)
    if row is None:
        return None
    state = ThrottleState(
        paused_until=row.paused_until,
        pause_reason=row.pause_reason,
        day=row.day,
        requests_today=row.requests_today,
    )
    try:
        wait_seconds(state, now, settings)
    except (WikidataPaused, WikidataBudgetSpent) as exc:
        return str(exc)
    return None


async def claim_run(
    db: AsyncSession, settings: Settings, now: datetime | None = None
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Hand out the next run, or None: one run at a time, and none while Wikidata waits."""
    now = now or datetime.now(UTC)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_LOCK})
    if await blocked(db, settings, now) is not None:
        return None
    busy = await db.scalar(
        select(exists().where(WikidataRun.status == "running", WikidataRun.claim_expires_at > now))
    )
    if busy:
        return None
    run: WikidataRun | None = await db.scalar(
        select(WikidataRun)
        .where(
            WikidataRun.kind.in_(WORKED),
            (WikidataRun.status == "queued")
            | ((WikidataRun.status == "running") & (WikidataRun.claim_expires_at <= now)),
        )
        # The user's buttons first, then refreshes, then the sweep; oldest first within each.
        .order_by(
            WikidataRun.entity_id.is_(None),
            WikidataRun.kind == "candidates",
            WikidataRun.created_at,
            WikidataRun.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if run is None:
        return None
    token = uuid.uuid4()
    run.status = "running"
    run.claim_token = token
    run.claim_expires_at = now + timedelta(seconds=settings.wikidata_run_lease_seconds)
    run.started_at = run.started_at or now
    await db.flush()
    return run.id, token


async def _update(
    run_id: uuid.UUID, token: uuid.UUID, settings: Settings, **values: object
) -> bool:
    """Write to the run if this worker still holds it; False when another took it over."""
    async with session_factory() as db, db.begin():
        run = await db.get(WikidataRun, run_id, with_for_update=True)
        if run is None or run.claim_token != token or run.status != "running":
            return False
        for key, value in values.items():
            if key in ("checked", "requests", "errors"):
                value = getattr(run, key) + int(value)  # type: ignore[call-overload]
            setattr(run, key, value)
        if run.status == "running":
            run.claim_expires_at = datetime.now(UTC) + timedelta(
                seconds=settings.wikidata_run_lease_seconds
            )
        else:
            run.claim_token = None
            run.claim_expires_at = None
        return True


async def process_run(
    run_id: uuid.UUID, token: uuid.UUID, client: WikidataClient, settings: Settings
) -> None:
    """Do one batch of the run: a root's search whole, or a sweep for wikidata_run_batch_seconds.

    A pause or the spent budget puts the run back in the queue where it stopped; a failed
    request fails it (the throttle has paused all traffic, and the sweep comes again).
    """
    async with session_factory() as db:
        run = await db.get(WikidataRun, run_id)
        if run is None or run.claim_token != token or run.status != "running":
            return
        entity_id, cursor = run.entity_id, run.cursor
    started = time.monotonic()
    sent = client.requests
    status = "queued"
    outcome: dict[str, object] = {"error": None}
    try:
        if entity_id is not None:
            await find_candidates(client, [entity_id], settings)
            outcome["checked"] = 1
            status = "finished"
        else:
            while time.monotonic() - started < settings.wikidata_run_batch_seconds:
                async with session_factory() as db:
                    root_ids = await eligible_roots(
                        db,
                        settings,
                        after=uuid.UUID(cursor) if cursor else None,
                        limit=SWEEP_BATCH,
                    )
                if not root_ids:
                    status = "finished"
                    break
                await find_candidates(client, root_ids, settings)
                cursor = str(root_ids[-1])
                # The place is saved after each batch; the claim is renewed with it.
                if not await _update(
                    run_id,
                    token,
                    settings,
                    cursor=cursor,
                    checked=len(root_ids),
                    requests=client.requests - sent,
                ):
                    return
                sent = client.requests
    except (WikidataPaused, WikidataBudgetSpent) as exc:
        outcome["error"] = str(exc)
    except WikidataUnavailable as exc:
        status = "failed"
        outcome.update(error=str(exc), errors=1)
    except WikidataDisabled as exc:
        status = "failed"
        outcome["error"] = str(exc)
    except WikidataError as exc:  # pragma: no cover - every kind is named above
        status = "failed"
        outcome["error"] = str(exc)
    finished = datetime.now(UTC) if status in ("finished", "failed") else None
    await _update(
        run_id,
        token,
        settings,
        status=status,
        finished_at=finished,
        requests=client.requests - sent,
        **outcome,
    )
    log.info("wikidata_run_batch", run_id=str(run_id), status=status, error=outcome["error"])


async def run_job(run_id: uuid.UUID, token: uuid.UUID) -> None:
    """The worker's entry: a client on the shared Postgres throttle, then one batch."""
    settings = get_settings()
    async with WikidataClient(settings, PostgresThrottle(settings)) as client:
        await process_run(run_id, token, client, settings)


def _send(run_id: str, token: str) -> None:
    from app.jobs.wikidata import process_wikidata_run

    process_wikidata_run.send(run_id, token)


async def schedule_wikidata(
    settings: Settings | None = None, *, send: Callable[[str, str], None] = _send
) -> int:
    """The scheduler's step: queue the day's sweep and hand one run to a worker."""
    settings = settings or get_settings()
    if not enabled(settings):
        return 0
    async with session_factory() as db, db.begin():
        await ensure_sweep(db, settings)
    async with session_factory() as db, db.begin():
        claimed = await claim_run(db, settings)
    if claimed is None:
        return 0
    run_id, token = claimed
    try:
        send(str(run_id), str(token))
    except Exception:
        # The claim expires, and the run is handed out again.
        log.exception("wikidata_queue_failed", run_id=str(run_id))
        return 0
    return 1
