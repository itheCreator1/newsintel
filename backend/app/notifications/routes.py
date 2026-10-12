from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.notifications.models import NotificationSettings
from app.notifications.ntfy import Message, NtfyError, publish
from app.notifications.schemas import NotificationSettingsResponse, NotificationSettingsUpdate
from app.notifications.service import investigations_href, settings_row

router = APIRouter(tags=["notifications"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


def _response(row: NotificationSettings) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        enabled=row.enabled,
        server=row.server,
        topic=row.topic,
        has_token=bool(row.token),
        public_url=row.public_url,
        timezone=row.timezone,
        quiet_start=row.quiet_start,
        quiet_end=row.quiet_end,
        digest_enabled=row.digest_enabled,
        digest_hour=row.digest_hour,
        last_sent_at=row.last_sent_at,
        last_error=row.last_error,
        last_error_at=row.last_error_at,
    )


@router.get("/notifications/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(db: Db, _auth: Auth) -> NotificationSettingsResponse:
    row = await settings_row(db)
    await db.commit()
    return _response(row)


@router.put("/notifications/settings", response_model=NotificationSettingsResponse)
async def put_notification_settings(
    payload: NotificationSettingsUpdate, db: Db, _session: Mutation
) -> NotificationSettingsResponse:
    """Replace the settings; the token is kept when left out and removed when empty."""
    row = await settings_row(db, lock=True)
    for field in (
        "enabled",
        "server",
        "topic",
        "public_url",
        "timezone",
        "quiet_start",
        "quiet_end",
        "digest_enabled",
        "digest_hour",
    ):
        setattr(row, field, getattr(payload, field))
    if "token" in payload.model_fields_set:
        row.token = (payload.token or "").strip() or None
    # New settings deserve a fresh try rather than the wait left by an earlier failure.
    row.retry_after = None
    await db.commit()
    await db.refresh(row)
    return _response(row)


@router.post("/notifications/test", status_code=status.HTTP_204_NO_CONTENT)
async def send_test_notification(db: Db, _session: Mutation) -> Response:
    """Send one message now with the saved settings, whether or not they are switched on."""
    row = await settings_row(db)
    await db.commit()
    if not (row.server and row.topic):
        raise HTTPException(status.HTTP_409_CONFLICT, "Save a server and a topic first")
    message = Message(
        title="NewsIntel test message",
        body="Notifications from NewsIntel reach this device.",
        click=investigations_href(row.public_url),
    )
    try:
        await publish(row.server, row.topic, row.token, message)
    except NtfyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
