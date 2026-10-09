"""One list of what every background process did or is doing, newest first.

Each process contributes a select with the same columns; the list is their UNION ALL, filtered,
ordered by (at, id) and paged with a keyset cursor. A row's title is what a person recognises
(an article's title, a feed's or an entity's name), never an id.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Integer,
    String,
    Uuid,
    case,
    cast,
    false,
    func,
    literal,
    literal_column,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import ClusterJob
from app.events.models import EventAssociationRun
from app.feeds.models import (
    Article,
    ArticleProcessingAttempt,
    ArticleProcessingJob,
    Feed,
    FeedFetch,
)
from app.feeds.service import decode_cursor, encode_cursor
from app.monitors.models import Monitor
from app.nlp.models import Entity, EntityAuthorityRun, NlpJob, NlpReprocessingRun
from app.operations.models import MaintenanceRun
from app.processes.schemas import (
    ActivityAttempt,
    ActivityCounts,
    ActivityFilter,
    ActivityItem,
    ActivityPage,
    ProcessKey,
)
from app.search.models import SearchDelivery, SearchIndexTarget, SearchRebuild, SourceSearchRefresh
from app.wikidata.models import WikidataRun

MESSAGE_LIMIT = 300
FINISHED = ("failed", "succeeded", "stopped")
FILTERS: dict[str, tuple[str, ...] | None] = {
    "attention": ("running", "retrying", "failed"),
    "failed": ("failed",),
    "running": ("running", "retrying"),
    "queued": ("queued",),
    "done": ("succeeded", "stopped"),
    "all": None,
}
COLUMNS = ("process", "id", "status", "title", "detail", "link_kind", "link_id", "at",
           "error_category", "error_message", "attempt_count")  # fmt: skip


def decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    return decode_cursor(cursor)


def _text(value: str | None) -> Any:
    return literal(value, type_=String) if value is not None else null().cast(String)


def _no_link() -> tuple[Any, Any]:
    return null().cast(String), null().cast(Uuid)


def _row(
    process: Any, id_: Any, status: Any, title: Any, detail: Any, link: tuple[Any, Any], at: Any,
    category: Any = None, message: Any = None, attempts: Any = None,
) -> list[Any]:  # fmt: skip
    """One process's columns, labelled and typed alike so the selects can be UNIONed."""
    values = (
        process if not isinstance(process, str) else literal(process, type_=String),
        id_, cast(status, String), cast(title, String), cast(detail, String),
        cast(link[0], String), cast(link[1], Uuid), at,
        cast(category, String) if category is not None else _text(None),
        cast(message, String) if message is not None else _text(None),
        cast(attempts, Integer) if attempts is not None else null().cast(Integer),
    )  # fmt: skip
    return [value.label(name) for value, name in zip(values, COLUMNS, strict=True)]


def _article_link() -> tuple[Any, Any]:
    return literal("article", type_=String), Article.id


def _sources(now: datetime, user_id: uuid.UUID | None) -> list[Any]:
    entity_name = func.coalesce(Entity.preferred_text, Entity.display_text)
    fetch, job, nlp, cluster, delivery = (
        FeedFetch, ArticleProcessingJob, NlpJob, ClusterJob, SearchDelivery
    )  # fmt: skip
    monitor = Monitor
    claimed = func.coalesce(monitor.claim_expires_at > now, False)
    monitor_status = case(
        (monitor.error_category.is_not(None), "failed"),
        (claimed, "running"),
        (monitor.next_evaluation_at <= now, "queued"),
        (monitor.last_evaluated_at.is_(None), "queued"),
        else_="succeeded",
    )
    reprocess = NlpReprocessingRun
    authority = EntityAuthorityRun
    rebuild = SearchRebuild
    refresh = SourceSearchRefresh
    events = EventAssociationRun
    cleanup = MaintenanceRun
    wikidata = WikidataRun
    removed: ColumnElement[Any] = literal_column(
        "(SELECT coalesce(sum(value::bigint), 0) FROM jsonb_each_text(maintenance_runs.deleted))"
    )
    return [
        select(*_row(
            "feeds", fetch.id, case((fetch.status == "success", "succeeded"), else_=fetch.status),
            Feed.name,
            case((fetch.status == "success",
                  func.concat(fetch.new_article_count, " new of ", fetch.entry_count, " entries")),
                 else_=null()),
            (literal("feed", type_=String), Feed.id),
            func.coalesce(fetch.completed_at, fetch.started_at),
            fetch.error_category, fetch.error_message, fetch.attempt_count,
        )).join(Feed, Feed.id == fetch.feed_id),
        select(*_row(
            "articles", job.id, job.status, Article.title,
            func.concat(job.stage, " · ", func.replace(job.requested_mode, "_", " ")),
            _article_link(), func.coalesce(job.completed_at, job.started_at, job.created_at),
            job.error_category, job.error_message,
        )).join(Article, Article.id == job.article_id),
        select(*_row(
            "nlp", nlp.id, nlp.status, Article.title, nlp.processor_name, _article_link(),
            func.coalesce(nlp.completed_at, nlp.created_at),
            nlp.error_category, nlp.error_message, nlp.attempt_count,
        )).join(Article, Article.id == nlp.article_id).where(nlp.status != "superseded"),
        select(*_row(
            "clustering", cluster.id, cluster.status, Article.title, _text(None),
            _article_link(), func.coalesce(cluster.completed_at, cluster.created_at),
            cluster.error_category, cluster.error_message, cluster.attempt_count,
        )).join(Article, Article.id == cluster.article_id).where(cluster.status != "superseded"),
        select(*_row(
            "search", delivery.id, delivery.status, Article.title, SearchIndexTarget.index_name,
            _article_link(), delivery.updated_at,
            delivery.error_category, delivery.error_message, delivery.attempt_count,
        )).join(Article, Article.id == delivery.article_id)
        .join(SearchIndexTarget, SearchIndexTarget.id == delivery.target_id),
        # A person's monitors are theirs: the list shows the signed-in user's own.
        select(*_row(
            "monitors", monitor.id, monitor_status, monitor.name, monitor.kind,
            (literal("monitor", type_=String), monitor.id), monitor.updated_at,
            monitor.error_category, monitor.error_message,
        )).where(
            monitor.enabled,
            monitor.user_id == user_id if user_id is not None else false(),
        ),
        select(*_row(
            "reprocessing", reprocess.id,
            case(
                (reprocess.status == "scanning", "running"),
                (reprocess.error_message.is_not(None), "failed"),
                (reprocess.status == "succeeded", "succeeded"),
                else_=reprocess.status,
            ),
            _text("NLP reprocessing"),
            func.concat(reprocess.scanned_count, " articles scanned, ",
                        reprocess.enqueued_count, " jobs queued"),
            _no_link(), func.coalesce(reprocess.completed_at, reprocess.created_at),
            null(), reprocess.error_message,
        )),
        select(*_row(
            "authority", authority.id,
            case((authority.status == "finished", "succeeded"), else_=authority.status),
            entity_name,
            case((authority.kind == "merge", "Merge into this name"),
                 (authority.kind == "split", "Split of this name"),
                 (authority.kind == "reindex", "Reindex after a rename"), else_=authority.kind),
            (literal("entity", type_=String), Entity.id),
            func.coalesce(authority.finished_at, authority.created_at),
        )).join(Entity, Entity.id == authority.entity_id),
        select(*_row(
            "rebuild", rebuild.id,
            case((rebuild.status == "completed", "succeeded"),
                 (rebuild.error_message.is_not(None), "failed"), else_="running"),
            _text("Search index rebuild"),
            func.concat(SearchIndexTarget.index_name, " · ", rebuild.scanned_count,
                        " articles scanned"),
            _no_link(), func.coalesce(rebuild.completed_at, rebuild.created_at),
            null(), rebuild.error_message,
        )).join(SearchIndexTarget, SearchIndexTarget.id == rebuild.target_id),
        select(*_row(
            "source_refresh", refresh.id, refresh.status, Feed.name,
            _text("Reindex after the source's name changed"),
            (literal("feed", type_=String), Feed.id), refresh.created_at,
            null(), refresh.error_message, refresh.attempt_count,
        )).join(Feed, Feed.id == refresh.feed_id),
        select(*_row(
            "events", events.id,
            case((events.error_category.is_not(None), "failed"), else_="succeeded"),
            _text("Event linking run"),
            func.concat(events.evaluated, " clusters checked, ", events.created,
                        " events created"),
            _no_link(), events.started_at, events.error_category, events.error_message,
        )),
        select(*_row(
            "retention", cleanup.id,
            case((cleanup.error_message.is_not(None), "failed"),
                 (cleanup.finished_at.is_(None), "running"), else_="succeeded"),
            _text("History cleanup"),
            case((cleanup.deleted.is_not(None), func.concat("Removed ", removed, " rows")),
                 else_=null()),
            _no_link(), cleanup.started_at, null(), cleanup.error_message,
        )).where(cleanup.kind == "retention"),
        select(*_row(
            case((wikidata.kind == "refresh", "wikidata_refresh"), else_="wikidata_candidates"),
            wikidata.id,
            case((wikidata.status == "finished", "succeeded"), else_=wikidata.status),
            func.coalesce(
                entity_name,
                case((wikidata.kind == "refresh", "All linked entities"), else_="New names"),
            ),
            case((wikidata.kind == "refresh",
                  func.concat(wikidata.checked, " checked, ", wikidata.changed, " changed")),
                 else_=func.concat(wikidata.checked, " names checked")),
            (case((wikidata.entity_id.is_not(None), "entity"), else_=null()), Entity.id),
            func.coalesce(wikidata.finished_at, wikidata.started_at, wikidata.created_at),
            null(), wikidata.error,
        )).outerjoin(Entity, Entity.id == wikidata.entity_id),
    ]  # fmt: skip


def _filtered(
    rows: Any, start: datetime, process: ProcessKey | None, q: str | None
) -> list[ColumnElement[bool]]:
    """The conditions every view shares: the window (finished rows only), process and text."""
    conditions: list[ColumnElement[bool]] = [
        or_(rows.c.status.not_in(FINISHED), rows.c.at >= start)
    ]
    if process is not None:
        conditions.append(rows.c.process == process)
    if q:
        conditions.append(
            or_(
                rows.c.title.icontains(q, autoescape=True),
                func.coalesce(rows.c.detail, "").icontains(q, autoescape=True),
            )
        )
    return conditions


def _status(rows: Any, name: str) -> ColumnElement[bool]:
    statuses = FILTERS[name]
    return true() if statuses is None else rows.c.status.in_(statuses)


def _cut(message: str | None) -> str | None:
    return None if message is None else message[:MESSAGE_LIMIT]


async def activity(
    db: AsyncSession, now: datetime, hours: int, *, process: ProcessKey | None,
    status: ActivityFilter, q: str | None, after: tuple[datetime, uuid.UUID] | None, limit: int,
    user_id: uuid.UUID | None,
) -> ActivityPage:  # fmt: skip
    start = now - timedelta(hours=hours)
    rows = union_all(*_sources(now, user_id)).subquery("activity")
    shared = _filtered(rows, start, process, q)
    page = select(rows).where(*shared, _status(rows, status))
    if after is not None:
        at, item_id = after
        page = page.where(or_(rows.c.at < at, (rows.c.at == at) & (rows.c.id < item_id)))
    found = (
        await db.execute(page.order_by(rows.c.at.desc(), rows.c.id.desc()).limit(limit + 1))
    ).all()
    counts = (
        await db.execute(
            select(*(func.count().filter(_status(rows, name)).label(name)
                     for name in ("attention", "failed", "running", "queued"))).where(*shared)
        )  # fmt: skip
    ).one()
    jobs = [row.id for row in found[:limit] if row.process == "articles"]
    attempts: dict[uuid.UUID, list[ActivityAttempt]] = {}
    a = ArticleProcessingAttempt
    for attempt in await db.scalars(
        select(a).where(a.job_id.in_(jobs)).order_by(a.job_id, a.attempt_number)
    ):
        attempts.setdefault(attempt.job_id, []).append(
            ActivityAttempt(
                number=attempt.attempt_number, stage=attempt.stage, status=attempt.status,
                error_category=attempt.error_category, error_message=_cut(attempt.error_message),
                started_at=attempt.started_at,
            )
        )  # fmt: skip
    items = []
    for row in found[:limit]:
        tried = attempts.get(row.id, [])
        items.append(
            ActivityItem(
                process=row.process, id=row.id, status=row.status, title=row.title,
                detail=row.detail, link_kind=row.link_kind, link_id=row.link_id, at=row.at,
                error_category=row.error_category, error_message=_cut(row.error_message),
                attempt_count=len(tried) if row.process == "articles" else row.attempt_count,
                attempts=tried,
            )
        )  # fmt: skip
    following = None
    if len(found) > limit:
        last = found[limit - 1]
        following = encode_cursor(last.at, last.id)
    return ActivityPage(
        generated_at=now, window_hours=hours, window_start=start, items=items,
        next_cursor=following, counts=ActivityCounts(**counts._mapping),
    )  # fmt: skip
