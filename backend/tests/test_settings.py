import pytest

from app.core.config import Settings


def test_production_rejects_insecure_session_cookie() -> None:
    with pytest.raises(ValueError, match="secure session cookies"):
        Settings(environment="production", session_cookie_secure=False)


def test_development_allows_http_localhost_cookie() -> None:
    settings = Settings(environment="development", session_cookie_secure=False)
    assert settings.session_cookie_secure is False
