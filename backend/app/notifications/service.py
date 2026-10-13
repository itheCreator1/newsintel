"""What watched investigations owe the user, and sending it: one message each, or a summary."""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import ColumnElement, and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_factory
from app.monitors.models import Monitor
from app.notifications import ntfy
from app.notifications.models import SETTINGS_ID, NotificationSettings
from app.notifications.ntfy import Message, NtfyError

log = structlog.get_logger()

RETRY_DELAY = timedelta(minutes=5)
LEASE = timedelta(minutes=2)
# Up to this many investigations get a message each; more are put together in one.
SEPARATE_MESSAGES = 3

Publish = Callable[[str, str, str | None, Message], Awaitable[None]]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or f'{one}s')}"


def monitor_href(public_url: str | None, monitor_id: uuid.UUID | str) -> str | None:
    return f"{public_url}/monitors/?id={monitor_id}" if public_url else None


def investigations_href(public_url: str | None) -> str | None:
    return f"{public_url}/investigations/" if public_url else None


def monitor_message(name: str, articles: int, link: str | None) -> Message:
    title = f"{_plural(articles, 'new article')}: {name}"
    return Message(
        title=title, body=f"“{name}” found {_plural(articles, 'new article')}.", click=link
    )


def combined_message(items: Sequence[tuple[str, int]], link: str | None) -> Message:
    lines = [f"{name}: {_plural(articles, 'new article')}" for name, articles in items]
    return Message(
        title=f"New articles in {len(items)} investigations", body="\n".join(lines), click=link
    )


def digest_message(items: Sequence[tuple[str, int, int]], link: str | None) -> Message:
    lines = [
        f"{name}: {_plural(articles, 'new article')}, "
        f"{_plural(stories, 'new story', 'new stories')}"
        for name, articles, stories in items
    ]
    count = len(items)
    has = "investigation has" if count == 1 else "investigations have"
    return Message(title=f"Daily summary: {count} {has} news", body="\n".join(lines), click=link)


def _local(now: datetime, zone: str) -> datetime:
    return now.astimezone(ZoneInfo(zone))


def in_quiet_hours(now: datetime, start: time | None, end: time | None, zone: str) -> bool:
    """Whether `now` falls in [start, end) on the zone's clock; an end before the start wraps."""
    if start is None or end is None or start == end:
        return False
    clock = _local(now, zone).time().replace(tzinfo=None)
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def digest_due(now: datetime, hour: int, zone: str, last_on: date | None) -> bool:
    """Once a local day, from the chosen hour on."""
    local = _local(now, zone)
    return local.hour >= hour and last_on != local.date()


async def settings_row(db: AsyncSession, *, lock: bool = False) -> NotificationSettings:
    """The one settings row, made with the defaults the first time it is asked for."""
    await db.execute(insert(NotificationSettings).values(id=SETTINGS_ID).on_conflict_do_nothing())
    query = select(NotificationSettings).where(NotificationSettings.id == SETTINGS_ID)
    row = await db.scalar(query.with_for_update() if lock else query)
    assert row is not None
    return row


def _ready(row: NotificationSettings) -> bool:
    return row.enabled and bool(row.server and row.topic)


async def notifications_on(db: AsyncSession) -> bool:
    row = await db.get(NotificationSettings, SETTINGS_ID)
    return row is not None and _ready(row)


def _owing() -> ColumnElement[bool]:
    return and_(
        Monitor.enabled.is_(True),
        Monitor.notify.is_(True),
        Monitor.pending_notification_articles > 0,
    )


def _digest_due(row: NotificationSettings, now: datetime) -> bool:
    return row.digest_enabled and digest_due(now, row.digest_hour, row.timezone, row.last_digest_on)


async def claim_delivery(db: AsyncSession, now: datetime) -> str | None:
    """Lease the settings row when something is due to be sent; None when nothing is."""
    row = await settings_row(db, lock=True)
    if not _ready(row):
        return None
    if row.retry_after is not None and row.retry_after > now:
        return None
    if row.claim_token is not None and row.claim_expires_at and row.claim_expires_at > now:
        return None
    due = _digest_due(row, now)
    if not due and not in_quiet_hours(now, row.quiet_start, row.quiet_end, row.timezone):
        due = bool(await db.scalar(select(func.count()).select_from(Monitor).where(_owing())))
    if not due:
        return None
    row.claim_token = uuid.uuid4().hex
    row.claim_expires_at = now + LEASE
    return row.claim_token


async def deliver(token: str, now: datetime, publish: Publish = ntfy.publish) -> None:  # noqa: C901
    """Send what the lease `token` covers: the daily summary when due, else what is owed."""
    async with session_factory() as db, db.begin():
        row = await settings_row(db)
        if row.claim_token != token or not _ready(row):
            return
        server, topic, secret, public_url = row.server, row.topic, row.token, row.public_url
        summary = _digest_due(row, now)
        quiet = in_quiet_hours(now, row.quiet_start, row.quiet_end, row.timezone)
        today = _local(now, row.timezone).date()
        # Each message, with the monitors and the counts it announces.
        outgoing: list[tuple[Message, list[tuple[uuid.UUID, int]]]] = []
        if summary:
            news = (
                await db.execute(
                    select(Monitor.name, Monitor.unseen_article_count, Monitor.unseen_cluster_count)
                    .where(Monitor.enabled.is_(True), Monitor.unseen_article_count > 0)
                    .order_by(Monitor.name, Monitor.id)
                )
            ).all()
            owed = (
                await db.execute(
                    select(Monitor.id, Monitor.pending_notification_articles).where(_owing())
                )
            ).all()
            covered = [(item.id, item.pending_notification_articles) for item in owed]
            if news:
                items = [
                    (item.name, item.unseen_article_count, item.unseen_cluster_count)
                    for item in news
                ]
                outgoing.append((digest_message(items, investigations_href(public_url)), covered))
        elif not quiet:
            pending = (
                await db.execute(
                    select(Monitor.id, Monitor.name, Monitor.pending_notification_articles)
                    .where(_owing())
                    .order_by(Monitor.name, Monitor.id)
                )
            ).all()
            if len(pending) > SEPARATE_MESSAGES:
                pairs = [(item.name, item.pending_notification_articles) for item in pending]
                covered = [(item.id, item.pending_notification_articles) for item in pending]
                outgoing.append((combined_message(pairs, investigations_href(public_url)), covered))
            else:
                for item in pending:
                    count = item.pending_notification_articles
                    message = monitor_message(item.name, count, monitor_href(public_url, item.id))
                    outgoing.append((message, [(item.id, count)]))
    assert server is not None and topic is not None

    sent_any = False
    failure: str | None = None
    for message, covered in outgoing:
        try:
            await publish(server, topic, secret, message)
        except NtfyError as exc:
            failure = str(exc)
            break
        sent_any = True
        await _announced(covered)
    if summary and failure is None and not outgoing:
        # A summary with no news still counts for today; what was owed is covered by it.
        await _announced(covered)

    async with session_factory() as db, db.begin():
        row = await settings_row(db, lock=True)
        if row.claim_token == token:
            row.claim_token = row.claim_expires_at = None
        if sent_any:
            row.last_sent_at = now
        if failure is None:
            row.last_error = row.last_error_at = row.retry_after = None
            if summary:
                row.last_digest_on = today
        else:
            log.warning("notification_failed", error=failure)
            row.last_error, row.last_error_at = failure, now
            row.retry_after = now + RETRY_DELAY


async def _announced(covered: Sequence[tuple[uuid.UUID, int]]) -> None:
    """Take what a message announced off what each monitor owes, keeping anything newer."""
    async with session_factory() as db, db.begin():
        for monitor_id, count in covered:
            await db.execute(
                update(Monitor)
                .where(Monitor.id == monitor_id)
                .values(
                    pending_notification_articles=func.greatest(
                        Monitor.pending_notification_articles - count, 0
                    )
                )
            )


def _send(token: str) -> None:
    from app.jobs.monitors import deliver_notifications

    deliver_notifications.send(token)


async def schedule_notifications(
    now: datetime | None = None, send: Callable[[str], None] = _send
) -> int:
    """The scheduler's step: hand a due delivery to a worker, one at a time."""
    now = now or datetime.now(UTC)
    async with session_factory() as db, db.begin():
        token = await claim_delivery(db, now)
    if token is None:
        return 0
    try:
        send(token)
    except Exception:
        # The lease expires and the delivery is claimed again.
        log.exception("notification_queue_failed")
        return 0
    return 1
