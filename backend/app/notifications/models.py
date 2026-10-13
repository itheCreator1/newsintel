from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    SmallInteger,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SETTINGS_ID = 1


class NotificationSettings(Base):
    """Where and when to push news from watched investigations; the app has one such row."""

    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_notification_settings_single_row"),
        CheckConstraint(
            "digest_hour BETWEEN 0 AND 23", name="ck_notification_settings_digest_hour"
        ),
        CheckConstraint(
            "(quiet_start IS NULL) = (quiet_end IS NULL)", name="ck_notification_settings_quiet"
        ),
    )

    # Always SETTINGS_ID; no sequence numbers it.
    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=SETTINGS_ID
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    server: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(64))
    # An access token for a protected topic; never sent back to the browser.
    token: Mapped[str | None] = mapped_column(Text)
    # The app's own address, so a message can open the investigation it is about.
    public_url: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    digest_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    digest_hour: Mapped[int] = mapped_column(SmallInteger, default=8, server_default="8")
    last_digest_on: Mapped[date | None] = mapped_column(Date)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # After a failed send, nothing is tried again before this.
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # One delivery at a time: the scheduler leases the row to the worker that sends.
    claim_token: Mapped[str | None] = mapped_column(String(64))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
