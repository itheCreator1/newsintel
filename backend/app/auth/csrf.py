import base64
import hashlib
import hmac
import secrets
import time


def issue_csrf_token(secret: str, now: int | None = None) -> str:
    timestamp = int(time.time()) if now is None else now
    payload = f"{timestamp}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


def verify_csrf_token(
    token: str, secret: str, now: int | None = None, lifetime_seconds: int = 600
) -> bool:
    try:
        timestamp_text, nonce, supplied = token.split(".", 2)
        timestamp = int(timestamp_text)
        current = int(time.time()) if now is None else now
        if timestamp > current or current - timestamp > lifetime_seconds:
            return False
        payload = f"{timestamp}.{nonce}"
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        return hmac.compare_digest(supplied, expected)
    except (ValueError, TypeError):
        return False
