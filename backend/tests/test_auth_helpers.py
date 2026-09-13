from datetime import UTC, datetime, timedelta

from app.auth.service import normalize_username, session_is_active


def test_username_is_trimmed_and_casefolded() -> None:
    assert normalize_username("  Alice  ") == "alice"


def test_expired_or_revoked_session_is_inactive() -> None:
    now = datetime.now(UTC)
    assert session_is_active(now + timedelta(seconds=1), None, now)
    assert not session_is_active(now - timedelta(seconds=1), None, now)
    assert not session_is_active(now + timedelta(seconds=1), now, now)
