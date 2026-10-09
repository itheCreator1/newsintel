"""One card per background process: a fixed set of aggregate statements over existing state.

Like the Operations queries (which the four job queues reuse), every number is a count, an age or
a time, and windowed numbers count what finished inside the window.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.events.models import EventAssociationRun
from app.feeds.models import Article, Feed, FeedFetch
from app.monitors.models import Monitor
from app.nlp.models import Entity, EntityAuthorityRun, NlpReprocessingRun
from app.nlp.reprocessing import selection_size
from app.operations import queries
from app.operations.models import MaintenanceRun
from app.processes import states
from app.processes.schemas import (
    ProcessCard,
    ProcessesResponse,
    ProcessGroup,
    ProcessKey,
    Progress,
)
from app.search.models import SearchRebuild, SourceSearchRefresh
from app.wikidata import runs as wikidata_runs
from app.wikidata.models import WikidataRun
from app.wikidata.status import disabled_reason


@dataclass(frozen=True)
class Process:
    key: ProcessKey
    group: ProcessGroup
    label: str
    description: str


PROCESSES = (
    Process("feeds", "per_item", "Feed fetching", "Each feed is fetched on its own schedule."),
    Process("articles", "per_item", "Article download",
            "Download each new article's page and extract its text."),
    Process("nlp", "per_item", "NLP",
            "Language, keywords, countries and entities of each article."),
    Process("clustering", "per_item", "Story clustering", "Place each article in a story."),
    Process("search", "per_item", "Search indexing", "Send each article to Elasticsearch."),
    Process("monitors", "per_item", "Watchlist monitors",
            "Check each enabled monitor for new matches."),
    Process("reprocessing", "bulk", "NLP reprocessing", "Run NLP again over existing articles."),
    Process("authority", "bulk", "Name changes",
            "Merge, split and rename runs that move articles between names."),
    Process("rebuild", "bulk", "Search index rebuild",
            "Build a new search index beside the live one, then switch to it."),
    Process("source_refresh", "bulk", "Source reindex",
            "Reindex a source's articles after its name changes."),
    Process("events", "scheduled", "Event linking", "Link stories to events, every 30 seconds."),
    Process("retention", "scheduled", "History cleanup",
            "Delete finished job history older than 30 days, every hour."),
    Process("wikidata_refresh", "scheduled", "Wikidata refresh",
            "Check linked Wikidata items for changes; each one monthly."),
    Process("wikidata_candidates", "scheduled", "Wikidata suggestions",
            "Look for Wikidata matches for names that appear often."),
)  # fmt: skip
_BY_KEY = {process.key: process for process in PROCESSES}
JOB_KEYS: dict[str, ProcessKey] = {
    "article": "articles", "search": "search", "nlp": "nlp", "clustering": "clustering",
}  # fmt: skip
AUTHORITY_KINDS = {"merge": "Merge into", "split": "Split of", "reindex": "Reindex of"}
MESSAGE_LIMIT = 300


def _card(key: ProcessKey, **values: Any) -> ProcessCard:
    process = _BY_KEY[key]
    return ProcessCard(
        key=key, group=process.group, label=process.label, description=process.description,
        **values,
    )  # fmt: skip


def _age(now: datetime, oldest: datetime | None) -> float | None:
    return None if oldest is None else max(0.0, (now - oldest).total_seconds())


def _cut(message: str | None) -> str | None:
    return None if message is None else message[:MESSAGE_LIMIT]


async def _newest(db: AsyncSession, query: Select[Any]) -> Any:
    return (await db.execute(query.limit(1))).first()


# -- one job queue per article ------------------------------------------------------------------


async def _jobs(db: AsyncSession, now: datetime, start: datetime) -> list[ProcessCard]:
    cards = []
    for spec in queries.JOB_SPECS:
        found = await queries.job_pipeline(db, spec, now, start)
        last = await db.scalar(select(func.max(spec.finished_at)))
        counts = {
            "queued": found.queued, "running": found.running, "retrying": found.retrying,
            "failed": found.failed, "lease_expired": found.lease_expired,
        }  # fmt: skip
        cards.append(
            _card(
                JOB_KEYS[spec.key],
                state=states.per_item(**counts, failed_in_window=found.failed_in_window),
                **counts, oldest_wait_seconds=found.oldest_wait_seconds,
                done_in_window=found.completed_in_window,
                failed_in_window=found.failed_in_window, last_run_at=last,
            )
        )  # fmt: skip
    return cards


async def _feeds(db: AsyncSession, now: datetime, start: datetime) -> ProcessCard:
    f = FeedFetch
    queued, running, done, bad, last = (
        await db.execute(
            select(
                func.count().filter(f.status == "queued"),
                func.count().filter(f.status == "running"),
                func.count().filter((f.status == "success") & (f.started_at >= start)),
                func.count().filter((f.status == "failed") & (f.started_at >= start)),
                func.max(f.started_at),
            )
        )
    ).one()
    newest = (
        select(FeedFetch.status)
        .where(FeedFetch.feed_id == Feed.id, FeedFetch.status.in_(("success", "failed")))
        .order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    live = Feed.enabled.is_(True) & Feed.retired_at.is_(None)
    failing, expired, oldest = (
        await db.execute(
            select(
                func.count().filter(newest == "failed"),
                func.count().filter(Feed.claim_token.is_not(None) & (Feed.claim_expires_at < now)),
                func.min(Feed.next_poll_at).filter(Feed.next_poll_at <= now),
            ).where(live)
        )
    ).one()
    counts = {"queued": queued, "running": running, "retrying": 0, "failed": failing,
              "lease_expired": expired}  # fmt: skip
    return _card(
        "feeds", state=states.per_item(**counts, failed_in_window=failing), **counts,
        oldest_wait_seconds=_age(now, oldest), done_in_window=done, failed_in_window=bad,
        last_run_at=last,
    )  # fmt: skip


async def _monitors(db: AsyncSession, now: datetime, start: datetime) -> ProcessCard:
    m = Monitor
    claimed = m.claim_expires_at > now
    due = m.enabled & (m.next_evaluation_at <= now) & ~func.coalesce(claimed, False)
    queued, running, failed, oldest, done, last = (
        await db.execute(
            select(
                func.count().filter(due),
                func.count().filter(claimed),
                func.count().filter(m.error_category.is_not(None)),
                func.min(m.next_evaluation_at).filter(due),
                func.count().filter(m.last_evaluated_at >= start),
                func.max(m.last_evaluated_at),
            )
        )
    ).one()
    counts = {"queued": queued, "running": running, "retrying": 0, "failed": failed,
              "lease_expired": 0}  # fmt: skip
    return _card(
        "monitors", state=states.per_item(**counts, failed_in_window=failed), **counts,
        oldest_wait_seconds=_age(now, oldest), done_in_window=done, last_run_at=last,
    )  # fmt: skip


# -- bulk runs ----------------------------------------------------------------------------------


def _processors(run: NlpReprocessingRun) -> str:
    names = ", ".join(str(name) for name in run.processor_names)
    language = run.selection.get("language")
    return f"{names} · {language} articles only" if isinstance(language, str) else names


async def _reprocessing(db: AsyncSession) -> ProcessCard:
    r = NlpReprocessingRun
    active = await db.scalar(
        select(r).where(r.status == "scanning").order_by(r.created_at.desc(), r.id.desc()).limit(1)
    )
    latest = await db.scalar(
        select(r).where(r.status != "scanning").order_by(r.created_at.desc(), r.id.desc()).limit(1)
    )
    failed = latest is not None and (latest.status == "failed" or latest.error_message is not None)
    if active is not None:
        total = await selection_size(db, active.selection)
        progress = Progress(done=active.scanned_count, total=total)
        detail = _processors(active)
    else:
        progress = None
        detail = None
        if latest is not None:
            detail = _cut(latest.error_message) if failed else _processors(latest)
    return _card(
        "reprocessing",
        state=states.run(active=active is not None, last_failed=failed, ran=latest is not None),
        last_run_at=None if latest is None else (latest.completed_at or latest.created_at),
        active_run_id=None if active is None else active.id, progress=progress, detail=detail,
    )  # fmt: skip


async def _authority(db: AsyncSession) -> ProcessCard:
    r = EntityAuthorityRun
    name = func.coalesce(Entity.preferred_text, Entity.display_text)
    query = select(r.id, r.kind, r.status, r.created_at, r.finished_at, name).join(
        Entity, Entity.id == r.entity_id
    )
    active = await _newest(
        db, query.where(r.status == "running").order_by(r.created_at.desc(), r.id.desc())
    )
    latest = await _newest(
        db, query.where(r.status != "running").order_by(r.created_at.desc(), r.id.desc())
    )
    shown = active or latest
    detail = None
    if shown is not None:
        detail = f"{AUTHORITY_KINDS.get(shown.kind, shown.kind)} “{shown[5]}”"
    return _card(
        "authority",
        state=states.run(
            active=active is not None,
            last_failed=latest is not None and latest.status == "failed",
            ran=latest is not None,
        ),
        last_run_at=None if latest is None else (latest.finished_at or latest.created_at),
        active_run_id=None if active is None else active.id, detail=detail,
    )  # fmt: skip


async def _rebuild(db: AsyncSession) -> ProcessCard:
    r = SearchRebuild
    active = await db.scalar(
        select(r).where(r.active_key == "active").order_by(r.created_at.desc()).limit(1)
    )
    latest = await db.scalar(
        select(r)
        .where(or_(r.active_key.is_(None), r.active_key != "active"))
        .order_by(r.created_at.desc(), r.id.desc())
        .limit(1)
    )
    failed = latest is not None and latest.status != "completed" and bool(latest.error_message)
    progress = None
    detail = None
    if active is not None:
        total = await db.scalar(select(func.count()).select_from(Article))
        progress = Progress(done=active.scanned_count, total=total or 0)
        detail = (
            "Catching up with new articles" if active.status == "catching_up" else "Scanning"
        )
    elif latest is not None and failed:
        detail = _cut(latest.error_message)
    return _card(
        "rebuild",
        state=states.run(active=active is not None, last_failed=failed, ran=latest is not None),
        last_run_at=None if latest is None else (latest.completed_at or latest.created_at),
        active_run_id=None if active is None else active.id, progress=progress, detail=detail,
    )  # fmt: skip


async def _source_refresh(db: AsyncSession, now: datetime, start: datetime) -> ProcessCard:
    r = SourceSearchRefresh
    queued, running, retrying, failed, bad, last = (
        await db.execute(
            select(
                func.count().filter(r.status == "queued"),
                func.count().filter(r.status == "running"),
                func.count().filter(r.status == "retrying"),
                func.count().filter(r.status == "failed"),
                func.count().filter((r.status == "failed") & (r.created_at >= start)),
                func.max(r.created_at),
            )
        )
    ).one()
    shown = await _newest(
        db,
        select(r.id, r.status, Feed.name, r.error_message)
        .join(Feed, Feed.id == r.feed_id)
        .order_by((r.status.in_(("queued", "running", "retrying"))).desc(), r.created_at.desc()),
    )
    detail = None
    if shown is not None:
        verb = {"failed": "Failed", "succeeded": "Reindexed"}.get(shown.status, "Reindexing")
        detail = f"{verb} {shown.name}"
        if shown.status == "failed" and shown.error_message:
            detail = f"{detail}: {_cut(shown.error_message)}"
    counts = {"queued": queued, "running": running, "retrying": retrying, "failed": failed,
              "lease_expired": 0}  # fmt: skip
    state = states.per_item(**counts, failed_in_window=bad)
    return _card(
        "source_refresh", state=state if last is not None else "idle", **counts,
        failed_in_window=bad, last_run_at=last, detail=detail,
    )  # fmt: skip


# -- scheduled ----------------------------------------------------------------------------------


async def _events(db: AsyncSession, start: datetime) -> ProcessCard:
    e = EventAssociationRun
    done, bad = (
        await db.execute(
            select(
                func.count().filter((e.started_at >= start) & e.error_category.is_(None)),
                func.count().filter((e.started_at >= start) & e.error_category.is_not(None)),
            )
        )
    ).one()
    latest = await db.scalar(select(e).order_by(e.started_at.desc(), e.id.desc()).limit(1))
    failed = latest is not None and latest.error_category is not None
    detail = None
    if latest is not None:
        detail = (
            _cut(latest.error_message or latest.error_category)
            if failed
            else f"{latest.evaluated:,} clusters checked, {latest.created:,} events created"
        )
    return _card(
        "events", state=states.run(active=False, last_failed=failed, ran=latest is not None),
        done_in_window=done, failed_in_window=bad,
        last_run_at=None if latest is None else latest.started_at, detail=detail,
    )  # fmt: skip


async def _retention(db: AsyncSession) -> ProcessCard:
    m = MaintenanceRun
    latest = await db.scalar(
        select(m).where(m.kind == "retention").order_by(m.started_at.desc(), m.id.desc()).limit(1)
    )
    detail = None
    running = latest is not None and latest.finished_at is None and latest.error_message is None
    failed = latest is not None and latest.error_message is not None
    if latest is not None and failed:
        detail = _cut(latest.error_message)
    elif latest is not None and latest.deleted is not None:
        rows = sum(latest.deleted.values())
        detail = f"Removed {rows:,} {'row' if rows == 1 else 'rows'}"
    return _card(
        "retention", state=states.run(active=running, last_failed=failed, ran=latest is not None),
        last_run_at=None if latest is None else latest.started_at, detail=detail,
    )  # fmt: skip


def _wikidata_detail(run: WikidataRun) -> str:
    if run.status == "failed":
        return _cut(run.error) or "Failed"
    if run.status == "stopped":
        return "Stopped by the user"
    if run.kind == "refresh":
        return f"{run.checked:,} checked, {run.changed:,} changed, {run.errors:,} errors"
    return f"{run.checked:,} names checked"


async def _wikidata(
    db: AsyncSession, now: datetime, settings: Settings
) -> list[ProcessCard]:
    reason = disabled_reason(settings)
    if reason is not None:
        keys: tuple[ProcessKey, ...] = ("wikidata_refresh", "wikidata_candidates")
        return [_card(key, state="off", detail=reason) for key in keys]
    waiting = await wikidata_runs.blocked(db, settings, now)
    cards = []
    kinds: tuple[tuple[str, ProcessKey], ...] = (
        ("refresh", "wikidata_refresh"), ("candidates", "wikidata_candidates")
    )  # fmt: skip
    for kind, key in kinds:
        r = WikidataRun
        mine = r.kind == kind
        queued, running = (
            await db.execute(
                select(
                    func.count().filter(r.status == "queued"),
                    func.count().filter(r.status == "running"),
                ).where(mine)
            )
        ).one()
        unfinished = r.status.in_(wikidata_runs.UNFINISHED)
        active = await db.scalar(
            select(r).where(mine, unfinished).order_by(r.created_at.desc(), r.id.desc()).limit(1)
        )
        latest = await db.scalar(
            select(r)
            .where(mine, ~unfinished)
            .order_by(func.coalesce(r.finished_at, r.created_at).desc(), r.id.desc())
            .limit(1)
        )
        state = states.run(
            active=active is not None,
            last_failed=latest is not None and latest.status == "failed",
            ran=latest is not None,
        )
        detail = None if latest is None else _wikidata_detail(latest)
        if active is not None:
            detail = f"{active.checked:,} checked so far" if active.checked else "Waiting to start"
            if waiting is not None:
                state, detail = "stalled", waiting
        cards.append(
            _card(
                key, state=state, queued=queued, running=running,
                last_run_at=None if latest is None else (latest.finished_at or latest.created_at),
                active_run_id=None if active is None else active.id, detail=detail,
            )
        )  # fmt: skip
    return cards


async def summary(
    db: AsyncSession, now: datetime, hours: int, settings: Settings
) -> ProcessesResponse:
    start = now - timedelta(hours=hours)
    cards = [
        await _feeds(db, now, start),
        *await _jobs(db, now, start),
        await _monitors(db, now, start),
        await _reprocessing(db),
        await _authority(db),
        await _rebuild(db),
        await _source_refresh(db, now, start),
        await _events(db, start),
        await _retention(db),
        *await _wikidata(db, now, settings),
    ]
    by_key = {card.key: card for card in cards}
    return ProcessesResponse(
        generated_at=now, window_hours=hours, window_start=start,
        processes=[by_key[process.key] for process in PROCESSES],
    )  # fmt: skip

