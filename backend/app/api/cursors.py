import uuid
from datetime import datetime

from fastapi import HTTPException

from app.feeds.service import decode_cursor


def cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    """Decode an optional keyset cursor, answering 400 when it is malformed."""
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None
