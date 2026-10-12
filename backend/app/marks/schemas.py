import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.marks.models import NOTE_LENGTH


class ArticleMarkResponse(BaseModel):
    article_id: uuid.UUID
    read: bool
    read_at: datetime | None
    starred: bool
    note: str | None


class ArticleMarkUpdate(BaseModel):
    """Only the fields sent change. An empty note clears it."""

    read: bool | None = None
    starred: bool | None = None
    note: str | None = Field(default=None, max_length=NOTE_LENGTH)

    @field_validator("note")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return (value or "").strip() or None
