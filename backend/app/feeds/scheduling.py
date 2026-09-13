from datetime import datetime


def lease_is_current(expected: str, supplied: str, expires_at: datetime, now: datetime) -> bool:
    return expected == supplied and expires_at > now


def next_retry_delay(attempt: int, retry_after: float | None = None, jitter: float = 0) -> float:
    base = retry_after if retry_after is not None else 2**attempt
    return min(300, max(0, base + jitter))
