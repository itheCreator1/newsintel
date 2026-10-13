"""Notifications end to end: what a monitor owes, when it is sent, and the settings behind it."""

import os
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import update
from test_monitor_api_postgres import CSRF, _client, _user
from test_monitor_evaluation_postgres import (
    FakeAdapter,
    _baselined,
    _claim,
    _read,
    _response,
    _set,
)

from app.db.session import session_factory
from app.monitors.evaluation import process_monitor
from app.monitors.models import Monitor
from app.notifications import routes as notification_routes
from app.notifications import service
from app.notifications.ntfy import Message, NtfyError

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

# Noon in Athens (UTC+3 in October): outside the quiet hours below, after the summary hour.
NOON = datetime(2026, 10, 12, 9, tzinfo=UTC)


class Outbox:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[Message] = []
        self.fail = fail

    async def __call__(
        self, server: str, topic: str, token: str | None, message: Message, **_: Any
    ) -> None:
        if self.fail:
            raise self.fail
        self.sent.append(message)


async def _configure(**values: Any) -> None:
    defaults: dict[str, Any] = {
        "enabled": True,
        "server": "https://ntfy.example",
        "topic": "news",
        "token": None,
        "public_url": "https://news.example",
        "timezone": "Europe/Athens",
        "quiet_start": time(23),
        "quiet_end": time(7),
        "digest_enabled": False,
        "digest_hour": 8,
        "last_digest_on": None,
        "retry_after": None,
        "claim_token": None,
        "claim_expires_at": None,
        "last_error": None,
        "last_error_at": None,
    }
    async with session_factory() as db, db.begin():
        row = await service.settings_row(db)
        for key, value in {**defaults, **values}.items():
            setattr(row, key, value)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _only_these_monitors_owe_anything() -> None:
    # Monitors from other modules stay in the database; nothing they owe may reach these outboxes.
    async with session_factory() as db, db.begin():
        await db.execute(update(Monitor).values(pending_notification_articles=0))


async def _counted(monitor_id: uuid.UUID, new: int, unseen: int, at: datetime) -> None:
    adapter = FakeAdapter(_response(new), _response(unseen, clusters=1))
    assert await process_monitor(monitor_id, await _claim(monitor_id), adapter, now=at)


async def _deliver(outbox: Outbox, now: datetime = NOON) -> None:
    async with session_factory() as db, db.begin():
        token = await service.claim_delivery(db, now)
    assert token is not None
    await service.deliver(token, now, publish=outbox)


async def test_new_articles_are_owed_once_and_a_view_or_mute_clears_them() -> None:
    await _configure()
    monitor_id, _ = await _baselined()
    at = datetime.now(UTC) + timedelta(minutes=10)

    await _counted(monitor_id, 3, 3, at)
    await _counted(monitor_id, 2, 5, at + timedelta(minutes=1))
    assert (await _read(monitor_id)).pending_notification_articles == 5

    async with _client((await _read(monitor_id)).user_id) as client:
        muted = await client.patch(
            f"/api/v1/monitors/{monitor_id}", json={"notify": False}, headers=CSRF
        )
        assert muted.status_code == 200 and muted.json()["notify"] is False
    assert (await _read(monitor_id)).pending_notification_articles == 0
    await _counted(monitor_id, 4, 9, at + timedelta(minutes=2))
    assert (await _read(monitor_id)).pending_notification_articles == 0

    await _set(monitor_id, notify=True)
    await _counted(monitor_id, 1, 10, at + timedelta(minutes=3))
    assert (await _read(monitor_id)).pending_notification_articles == 1
    item = await _read(monitor_id)
    async with _client(item.user_id) as client:
        seen = await client.post(
            f"/api/v1/monitors/{monitor_id}/viewed",
            json={"through": item.eval_cursor_at.isoformat()},  # type: ignore[union-attr]
            headers=CSRF,
        )
        assert seen.status_code == 200
    assert (await _read(monitor_id)).pending_notification_articles == 0


async def test_nothing_is_owed_while_notifications_are_off() -> None:
    await _configure(enabled=False)
    monitor_id, _ = await _baselined()

    await _counted(monitor_id, 3, 3, datetime.now(UTC) + timedelta(minutes=10))

    assert (await _read(monitor_id)).pending_notification_articles == 0


async def test_each_investigation_gets_its_own_message_and_many_get_one() -> None:
    await _configure()
    first, _ = await _baselined()
    second, _ = await _baselined()
    await _set(first, name=f"Energy {uuid.uuid4().hex[:6]}", pending_notification_articles=3)
    await _set(second, pending_notification_articles=1)
    first_name = (await _read(first)).name

    outbox = Outbox()
    await _deliver(outbox)

    titles = sorted(message.title for message in outbox.sent)
    assert titles == sorted([f"3 new articles: {first_name}", "1 new article: Grid"])
    assert {message.click for message in outbox.sent} == {
        f"https://news.example/monitors/?id={first}",
        f"https://news.example/monitors/?id={second}",
    }
    assert (await _read(first)).pending_notification_articles == 0
    async with session_factory() as db:
        assert (await service.settings_row(db)).last_sent_at == NOON

    many = [(await _baselined())[0] for _ in range(4)]
    for monitor_id in many:
        await _set(monitor_id, pending_notification_articles=2)
    outbox = Outbox()
    await _deliver(outbox)
    (combined,) = outbox.sent
    assert combined.title == "New articles in 4 investigations"
    assert combined.click == "https://news.example/investigations/"


async def test_quiet_hours_hold_messages_until_they_end() -> None:
    await _configure()
    monitor_id, _ = await _baselined()
    await _set(monitor_id, pending_notification_articles=2)
    night = datetime(2026, 10, 12, 0, tzinfo=UTC)  # 03:00 in Athens

    async with session_factory() as db, db.begin():
        assert await service.claim_delivery(db, night) is None
    assert (await _read(monitor_id)).pending_notification_articles == 2

    outbox = Outbox()
    await _deliver(outbox, night.replace(hour=5))  # 08:00 in Athens
    assert [message.title for message in outbox.sent] == ["2 new articles: Grid"]


async def test_the_daily_summary_lists_every_watched_investigation_with_news_once() -> None:
    await _configure(digest_enabled=True, last_digest_on=date(2026, 10, 11))
    async with session_factory() as db, db.begin():
        # Only this test's monitors have news, so the summary is about them alone.
        await db.execute(update(Monitor).values(unseen_article_count=0, unseen_cluster_count=0))
    busy, _ = await _baselined()
    paused, _ = await _baselined()
    await _set(
        busy, unseen_article_count=5, unseen_cluster_count=1, pending_notification_articles=2
    )
    await _set(paused, unseen_article_count=4, enabled=False)

    outbox = Outbox()
    await _deliver(outbox)

    (summary,) = outbox.sent
    assert summary.title == "Daily summary: 1 investigation has news"
    assert summary.body == "Grid: 5 new articles, 1 new story"
    # The summary covered what was owed; it is not sent again the same day.
    assert (await _read(busy)).pending_notification_articles == 0
    async with session_factory() as db, db.begin():
        assert (await service.settings_row(db)).last_digest_on == date(2026, 10, 12)
        assert await service.claim_delivery(db, NOON + timedelta(hours=1)) is None


async def test_a_failed_send_keeps_what_is_owed_and_waits_before_trying_again() -> None:
    await _configure()
    monitor_id, _ = await _baselined()
    await _set(monitor_id, pending_notification_articles=2)

    await _deliver(Outbox(fail=NtfyError("ntfy answered 403: forbidden")))

    assert (await _read(monitor_id)).pending_notification_articles == 2
    async with session_factory() as db, db.begin():
        row = await service.settings_row(db)
        assert (row.last_error, row.last_error_at) == ("ntfy answered 403: forbidden", NOON)
        assert row.retry_after == NOON + service.RETRY_DELAY
        assert await service.claim_delivery(db, NOON + timedelta(minutes=1)) is None
    outbox = Outbox()
    await _deliver(outbox, NOON + service.RETRY_DELAY)
    assert len(outbox.sent) == 1
    async with session_factory() as db:
        assert (await service.settings_row(db)).last_error is None


async def test_the_scheduler_hands_a_due_delivery_to_a_worker_once() -> None:
    await _configure()
    monitor_id, _ = await _baselined()
    await _set(monitor_id, pending_notification_articles=1)
    queued: list[str] = []

    assert await service.schedule_notifications(now=NOON, send=queued.append) == 1
    assert await service.schedule_notifications(now=NOON, send=queued.append) == 0
    assert len(queued) == 1

    await service.deliver(queued[0], NOON, publish=Outbox())
    assert await service.schedule_notifications(now=NOON, send=queued.append) == 0


async def test_settings_round_trip_without_ever_returning_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _configure(enabled=False, token=None)
    body = {
        "enabled": True,
        "server": "https://ntfy.example",
        "topic": "news",
        "token": "tk_secret",
        "public_url": "https://news.example",
        "timezone": "Europe/Athens",
        "quiet_start": "23:00:00",
        "quiet_end": "07:00:00",
        "digest_enabled": True,
        "digest_hour": 8,
    }
    async with _client(await _user()) as client:
        saved = await client.put("/api/v1/notifications/settings", json=body, headers=CSRF)
        assert saved.status_code == 200, saved.text
        assert saved.json()["has_token"] is True and "token" not in saved.json()
        kept = {key: value for key, value in body.items() if key != "token"}
        again = await client.put("/api/v1/notifications/settings", json=kept, headers=CSRF)
        assert again.json()["has_token"] is True
        cleared = await client.put(
            "/api/v1/notifications/settings", json={**kept, "token": ""}, headers=CSRF
        )
        assert cleared.json()["has_token"] is False
        refused = await client.put(
            "/api/v1/notifications/settings", json={**kept, "topic": "a b"}, headers=CSRF
        )
        assert refused.status_code == 422

        outbox = Outbox()
        monkeypatch.setattr(notification_routes, "publish", outbox)
        test = await client.post("/api/v1/notifications/test", headers=CSRF)
        assert test.status_code == 204
        assert [message.title for message in outbox.sent] == ["NewsIntel test message"]
        monkeypatch.setattr(notification_routes, "publish", Outbox(fail=NtfyError("no route")))
        failed = await client.post("/api/v1/notifications/test", headers=CSRF)
        assert failed.status_code == 502 and failed.json()["detail"] == "no route"
        current = (await client.get("/api/v1/notifications/settings")).json()
    assert current["quiet_start"] == "23:00:00" and current["digest_hour"] == 8
