import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any, overload

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


# Signed cursors: an opaque, tamper-proof page token for searches that page through a snapshot.
def sign_cursor(payload: dict[str, Any], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode()


def read_cursor(value: str, secret: str) -> dict[str, Any]:
    try:
        decoded = base64.urlsafe_b64decode(value.encode())
        raw, signature = decoded[:-32], decoded[-32:]
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        result: dict[str, Any] = json.loads(raw)
        return result
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "Invalid search cursor; restart the search") from exc
