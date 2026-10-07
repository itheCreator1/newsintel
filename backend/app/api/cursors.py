import uuid
from datetime import datetime
from typing import overload

from fastapi import HTTPException

from app.feeds.service import decode_cursor


@overload
def cursor_or_400(cursor: str) -> tuple[datetime, uuid.UUID]: ...
@overload
def cursor_or_400(cursor: None) -> None: ...
@overload
def cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None: ...


def cursor_or_400(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    """Decode an optional keyset cursor, answering 400 when it is malformed."""
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None
