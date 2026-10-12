import re
from datetime import datetime, time
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, field_validator, model_validator

TOPIC = re.compile(r"^[-_A-Za-z0-9]{1,64}$")


def _web_address(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", value):
        raise ValueError("must be an http:// or https:// address")
    return value


class NotificationSettingsUpdate(BaseModel):
    enabled: bool = False
    server: str | None = None
    topic: str | None = None
    # Left out: keep the stored token. Empty: remove it.
    token: str | None = None
    public_url: str | None = None
    timezone: str = "UTC"
    quiet_start: time | None = None
    quiet_end: time | None = None
    digest_enabled: bool = False
    digest_hour: int = 8

    @field_validator("server", "public_url")
    @classmethod
    def _address(cls, value: str | None) -> str | None:
        return _web_address(value)

    @field_validator("topic")
    @classmethod
    def _topic(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not TOPIC.match(value.strip()):
            raise ValueError("may use only letters, digits, - and _ (at most 64)")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @field_validator("digest_hour")
    @classmethod
    def _hour(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("must be an hour from 0 to 23")
        return value

    @model_validator(mode="after")
    def _whole(self) -> Self:
        if (self.quiet_start is None) != (self.quiet_end is None):
            raise ValueError("quiet hours need both a start and an end")
        if self.enabled and not (self.server and self.topic):
            raise ValueError("notifications need a server and a topic")
        return self


class NotificationSettingsResponse(BaseModel):
    enabled: bool
    server: str | None
    topic: str | None
    has_token: bool
    public_url: str | None
    timezone: str
    quiet_start: time | None
    quiet_end: time | None
    digest_enabled: bool
    digest_hour: int
    last_sent_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
