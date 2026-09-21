"""Operational metrics, each a small fixed set of aggregate statements over existing state.

Nothing here scores or ranks: every number is a count, an age or a duration with its definition,
and windowed numbers say which timestamp they are windowed by.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, String, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.clustering.models import ClusterJob
from app.events.engine import EVENT_ALGORITHM_VERSION, dirty_clusters
from app.events.models import EventAssociationRun
from app.feeds.models import ArticleContent, ArticleProcessingJob, Feed, FeedFetch
from app.monitors.models import Monitor
from app.nlp.models import NlpJob, NlpProcessorRun
from app.operations.schemas import (
    Area,
    CategoryCount,
    EventPipeline,
    FailureItem,
    FailuresResponse,
    FeedHealth,
    FeedsResponse,
    FeedState,
    FeedTotals,
    JobPipeline,
    MonitorPipeline,
    PipelinesResponse,
    RunError,
    StorageResponse,
    TableSize,
)
from app.search.models import SearchDelivery

STREAK_LOOKBACK = 20
FEED_LIMIT = 500
MESSAGE_LIMIT = 300
ACTIVE = ("queued", "running", "retrying")
STORAGE_TABLES = (
    "articles", "article_contents", "feed_articles", "feed_fetches", "article_processing_jobs",
    "article_processing_attempts", "search_deliveries", "nlp_jobs", "nlp_processor_runs",
    "article_nlp_entities", "article_nlp_keywords", "article_country_annotations", "cluster_jobs",
    "story_clusters", "story_cluster_members", "events", "event_clusters", "monitors",
    "event_association_runs",
)  # fmt: skip


@dataclass(frozen=True)
class JobSpec:
    key: str
    label: str
    model: Any
    definition: str
    window_basis: str
    # The timestamp a job finished (or last changed) at, which decides the window.
    finished_at: InstrumentedAttribute[Any]


JOB_SPECS = (
    JobSpec(
        "article", "Article fetch and extraction", ArticleProcessingJob,
        "Jobs that download an article page and extract its text.",
        "jobs finished in the window (completed_at)", ArticleProcessingJob.completed_at,
    ),
    JobSpec(
        "search", "Search indexing", SearchDelivery,
        "Deliveries of an article revision to an Elasticsearch index. A transient Elasticsearch "
        "outage keeps deliveries retrying rather than failed, so a growing retrying count is the "
        "sign to look for.",
        "deliveries that changed in the window (updated_at)", SearchDelivery.updated_at,
    ),
    JobSpec(
        "nlp", "NLP processing", NlpJob,
        "Language, keyword, country and entity processing of an article's text.",
        "processor runs finished in the window (nlp_processor_runs.completed_at)",
        NlpProcessorRun.completed_at,
    ),
    JobSpec(
        "clustering", "Story clustering", ClusterJob,
        "Placing an article into a story cluster.",
        "jobs finished in the window (completed_at)", ClusterJob.completed_at,
    ),
)  # fmt: skip


def _cut(message: str | None) -> str | None:
    return None if message is None else message[:MESSAGE_LIMIT]


def _window(now: datetime, hours: int) -> datetime:
    return now - timedelta(hours=hours)


async def _job_pipeline(
    db: AsyncSession, spec: JobSpec, now: datetime, start: datetime
) -> JobPipeline:
    m = spec.model
    due = m.status.in_(("queued", "retrying")) & (m.next_attempt_at <= now)
    queued, running, retrying, failed, expired, oldest = (
        await db.execute(
            select(
                func.count().filter(m.status == "queued"),
                func.count().filter(m.status == "running"),
                func.count().filter(m.status == "retrying"),
                func.count().filter(m.status == "failed"),
                func.count().filter((m.status == "running") & (m.claim_expires_at < now)),
                func.min(m.next_attempt_at).filter(due),
            ).where(m.status.in_((*ACTIVE, "failed")))
        )
    ).one()
    if spec.key == "nlp":  # failed jobs record no end time; the run that failed does
        r = NlpProcessorRun
        window = select(
            func.count().filter(r.outcome != "failed"), func.count().filter(r.outcome == "failed")
        ).where(r.completed_at >= start)
    else:
        window = select(
            func.count().filter(m.status == "succeeded"), func.count().filter(m.status == "failed")
        ).where(spec.finished_at >= start)
    done, bad = (await db.execute(window)).one()
    return JobPipeline(
        key=spec.key, label=spec.label, definition=spec.definition, window_basis=spec.window_basis,
        queued=queued, running=running, retrying=retrying, failed=failed, lease_expired=expired,
        oldest_wait_seconds=None if oldest is None else (now - oldest).total_seconds(),
        completed_in_window=done, failed_in_window=bad,
    )  # fmt: skip


async def _events(db: AsyncSession, now: datetime, start: datetime) -> EventPipeline:
    run = EventAssociationRun
    dirty = await db.scalar(
        select(func.count()).select_from(dirty_clusters(EVENT_ALGORITHM_VERSION).subquery())
    )
    last_run, last_ok, in_window, bad_runs, bad_clusters = (
        await db.execute(
            select(
                func.max(run.started_at),
                func.max(run.started_at).filter(run.error_category.is_(None)),
                func.count().filter(run.started_at >= start),
                func.count().filter((run.started_at >= start) & run.error_category.is_not(None)),
                func.coalesce(func.sum(run.failed).filter(run.started_at >= start), 0),
            )
        )
    ).one()
    latest = (
        await db.execute(
            select(run.started_at, run.error_category, run.error_message)
            .where(run.error_category.is_not(None))
            .order_by(run.started_at.desc())
            .limit(1)
        )
    ).first()
    return EventPipeline(
        label="Event association",
        definition=(
            "Clusters of two or more articles that are new or changed since their last event "
            "decision (dirty), and the runs that decide them. A run is recorded only when it did "
            "work or failed; a cluster that fails stays dirty and is retried."
        ),
        algorithm_version=EVENT_ALGORITHM_VERSION,
        dirty_clusters=dirty or 0, last_run_at=last_run, last_success_at=last_ok,
        runs_in_window=in_window, failed_runs_in_window=bad_runs,
        failed_clusters_in_window=int(bad_clusters),
        last_error=None if latest is None else RunError(
            at=latest[0], category=latest[1], message=_cut(latest[2])
        ),
    )  # fmt: skip


async def _monitors(db: AsyncSession, now: datetime) -> MonitorPipeline:
    m = Monitor
    overdue = m.enabled & (m.next_evaluation_at <= now)
    total, enabled, due, oldest, in_error = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(m.enabled),
                func.count().filter(overdue),
                func.min(m.next_evaluation_at).filter(overdue),
                func.count().filter(m.error_category.is_not(None)),
            )
        )
    ).one()
    categories = (
        await db.execute(
            select(m.error_category, func.count())
            .where(m.error_category.is_not(None))
            .group_by(m.error_category)
            .order_by(func.count().desc(), m.error_category)
        )
    ).all()
    return MonitorPipeline(
        label="Monitors",
        definition=(
            "Watches evaluated on a schedule. Due means enabled with an evaluation time that has "
            "passed."
        ),
        total=total, enabled=enabled, due=due, in_error=in_error,
        oldest_overdue_seconds=None if oldest is None else (now - oldest).total_seconds(),
        by_error_category=[CategoryCount(category=c, count=n) for c, n in categories],
    )  # fmt: skip


async def pipelines(db: AsyncSession, now: datetime, hours: int) -> PipelinesResponse:
    start = _window(now, hours)
    jobs = [await _job_pipeline(db, spec, now, start) for spec in JOB_SPECS]
    return PipelinesResponse(
        generated_at=now, window_hours=hours, window_start=start, jobs=jobs,
        events=await _events(db, now, start), monitors=await _monitors(db, now),
    )  # fmt: skip


# One row per feed: the newest STREAK_LOOKBACK finished fetches decide the streak. Fetches that are
# still queued are not results yet, so they neither extend nor break a streak.
FEED_SQL = text(
    """
    SELECT f.id, f.name, f.enabled, f.poll_interval_minutes, f.last_success_at, f.next_poll_at,
           r.n, r.last_status, r.last_at, r.first_success_rank
    FROM feeds f
    LEFT JOIN LATERAL (
        SELECT count(*) AS n,
               max(status) FILTER (WHERE rn = 1) AS last_status,
               max(started_at) FILTER (WHERE rn = 1) AS last_at,
               min(rn) FILTER (WHERE status = 'success') AS first_success_rank
        FROM (
            SELECT status, started_at,
                   row_number() OVER (ORDER BY started_at DESC, id DESC) AS rn
            FROM feed_fetches
            WHERE feed_id = f.id AND status IN ('success', 'failed')
            ORDER BY started_at DESC, id DESC
            LIMIT :lookback
        ) recent
    ) r ON true
    WHERE f.retired_at IS NULL
    ORDER BY lower(f.name), f.id
    LIMIT :limit
    """
)


def _feed_state(
    enabled: bool, streak: int, overdue: int | None, interval: int, seen: bool
) -> FeedState:
    if not enabled:
        return "disabled"
    if streak:
        return "failing"
    if overdue is not None and overdue > interval * 60:
        return "overdue"
    return "ok" if seen else "awaiting"


async def feeds(db: AsyncSession, now: datetime, hours: int) -> FeedsResponse:
    start = _window(now, hours)
    rows = (
        await db.execute(FEED_SQL, {"lookback": STREAK_LOOKBACK, "limit": FEED_LIMIT + 1})
    ).all()
    fetch = FeedFetch
    failures: dict[uuid.UUID, dict[str, int]] = {}
    category = func.coalesce(fetch.error_category, "unknown")
    for feed_id, name, count in (
        await db.execute(
            select(fetch.feed_id, category, func.count())
            .where(fetch.status == "failed", fetch.started_at >= start)
            .group_by(fetch.feed_id, category)
        )
    ).all():
        failures.setdefault(feed_id, {})[name] = count
    entries, invalid, new, finished = (
        await db.execute(
            select(
                func.coalesce(func.sum(fetch.entry_count), 0),
                func.coalesce(func.sum(fetch.invalid_entry_count), 0),
                func.coalesce(func.sum(fetch.new_article_count), 0),
                func.count(),
            )
            .join(Feed, Feed.id == fetch.feed_id)
            .where(
                Feed.retired_at.is_(None),
                fetch.status.in_(("success", "failed")),
                fetch.started_at >= start,
            )
        )
    ).one()
    items = []
    for row in rows[:FEED_LIMIT]:
        finished_fetches = row.n or 0
        streak = finished_fetches if row.first_success_rank is None else row.first_success_rank - 1
        overdue = int((now - row.next_poll_at).total_seconds()) if row.enabled else None
        overdue = overdue if overdue is not None and overdue > 0 else None
        items.append(
            FeedHealth(
                id=row.id, name=row.name, enabled=row.enabled,
                poll_interval_minutes=row.poll_interval_minutes,
                state=_feed_state(
                    row.enabled, streak, overdue, row.poll_interval_minutes,
                    finished_fetches > 0 or row.last_success_at is not None,
                ),
                last_fetch_status=row.last_status, last_fetch_at=row.last_at,
                last_success_at=row.last_success_at, next_poll_at=row.next_poll_at,
                overdue_seconds=overdue, failure_streak=streak,
                streak_capped=(
                    row.first_success_rank is None and finished_fetches == STREAK_LOOKBACK
                ),
                failures_by_category=failures.get(row.id, {}),
            )
        )  # fmt: skip
    return FeedsResponse(
        generated_at=now, window_hours=hours, window_start=start,
        truncated=len(rows) > FEED_LIMIT, items=items,
        totals=FeedTotals(
            fetches=finished, entries=entries, invalid=invalid, new=new,
            duplicates=entries - invalid - new,
        ),
    )  # fmt: skip


# -- failures -----------------------------------------------------------------------------------


def _job_failures(model: Any, at: Any, statuses: tuple[str, ...]):  # type: ignore[no-untyped-def]
    category = func.coalesce(model.error_category, "unknown")
    return (
        select(
            model.id, model.article_id.label("ref"), model.status, at.label("at"),
            category.label("category"), model.error_message.label("message"),
        ).where(model.status.in_(statuses)),
        at,
        category,
    )  # fmt: skip


no_ref = literal(None, type_=Integer).label("ref")
no_status = literal(None, type_=String).label("status")


def _failure_sources(area: Area):  # type: ignore[no-untyped-def]
    """The select of failure rows for one area, its time column and its category expression."""
    if area == "feed":
        f = FeedFetch
        category = func.coalesce(f.error_category, "unknown")
        return (
            select(
                f.id, f.feed_id.label("ref"), f.status, f.started_at.label("at"),
                category.label("category"), f.error_message.label("message"),
            ).where(f.status == "failed"),
            f.started_at,
            category,
        )  # fmt: skip
    if area == "article":
        return _job_failures(ArticleProcessingJob, ArticleProcessingJob.completed_at, ("failed",))
    if area == "search":  # retrying deliveries with a cause are the visible face of an outage
        return _job_failures(SearchDelivery, SearchDelivery.updated_at, ("failed", "retrying"))
    if area == "cluster":
        return _job_failures(ClusterJob, ClusterJob.completed_at, ("failed",))
    if area == "nlp":
        r = NlpProcessorRun
        # Runs from before migration 0014 carry no category; they fall back to their job's.
        category = func.coalesce(r.error_category, NlpJob.error_category, "unknown")
        return (
            select(
                r.id, r.article_id.label("ref"), literal("failed").label("status"),
                r.completed_at.label("at"), category.label("category"), r.detail.label("message"),
            ).join(NlpJob, NlpJob.id == r.job_id).where(r.outcome == "failed"),
            r.completed_at,
            category,
        )  # fmt: skip
    if area == "event":
        e = EventAssociationRun
        category = func.coalesce(e.error_category, "unknown")
        return (
            select(
                e.id, no_ref, no_status, e.started_at.label("at"), category.label("category"),
                e.error_message.label("message"),
            ).where(e.error_category.is_not(None)),
            e.started_at,
            category,
        )  # fmt: skip
    m = Monitor  # a monitor's error is its current state; the owner, name and message stay private
    return (
        select(
            m.id, no_ref, no_status,
            m.updated_at.label("at"), m.error_category.label("category"),
            literal(None, type_=String).label("message"),
        ).where(m.error_category.is_not(None)),
        m.updated_at,
        m.error_category,
    )  # fmt: skip


async def failures(
    db: AsyncSession, now: datetime, area: Area, hours: int, limit: int
) -> FailuresResponse:
    start = _window(now, hours)
    base, at, category = _failure_sources(area)
    counts = (await db.execute(_counts_statement(base, at, category, start))).all()
    rows = (
        await db.execute(base.where(at >= start).order_by(at.desc(), text("1")).limit(limit))
    ).all()
    return FailuresResponse(
        generated_at=now, area=area, window_hours=hours, window_start=start,
        by_category=[CategoryCount(category=c, count=n) for c, n in counts],
        recent=[
            FailureItem(
                id=row.id, ref_id=row.ref, status=row.status, at=row.at,
                error_category=row.category, message=_cut(row.message),
            )
            for row in rows
        ],
    )  # fmt: skip


def _counts_statement(base: Any, at: Any, category: Any, start: datetime) -> Any:
    return (
        base.with_only_columns(category, func.count())
        .where(at >= start)
        .group_by(category)
        .order_by(func.count().desc(), category)
    )


# -- storage ------------------------------------------------------------------------------------


async def storage(db: AsyncSession, now: datetime | None = None) -> StorageResponse:
    """Sizes from the catalogue for a fixed list of tables (never user input). Row counts are the
    planner's estimates: exact counts would scan the big tables."""
    rows = (
        await db.execute(
            text(
                """
                SELECT c.relname, pg_total_relation_size(c.oid) AS bytes,
                       greatest(c.reltuples, 0)::bigint AS approximate_rows
                FROM pg_class c
                WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace
                  AND c.relname = ANY(:names)
                """
            ),
            {"names": list(STORAGE_TABLES)},
        )
    ).all()
    found = {r.relname: r for r in rows}
    database_bytes = await db.scalar(text("SELECT pg_database_size(current_database())"))
    retained = await db.scalar(select(func.count(ArticleContent.html_object_key)))
    return StorageResponse(
        generated_at=now or datetime.now(UTC),
        database_bytes=database_bytes or 0,
        tables=[
            TableSize(
                name=n, total_bytes=found[n].bytes, approximate_rows=found[n].approximate_rows
            )
            for n in STORAGE_TABLES
            if n in found
        ],
        retained_html_objects=retained or 0,
        article_files_measured=False,
        article_files_note=(
            "Retained article HTML lives on the worker's volume, which the API cannot read, so "
            "only the number of stored objects is shown. To size it, run on the host: docker "
            "compose exec worker du -sh /var/lib/newsintel/articles"
        ),
        elasticsearch=None, elasticsearch_error=None,
    )  # fmt: skip
