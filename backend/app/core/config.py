from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NEWSINTEL_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel"
    redis_url: str = "redis://redis:6379/0"
    elasticsearch_url: str = "http://elasticsearch:9200"
    session_cookie_name: str = "newsintel_session"
    session_cookie_secure: bool = False
    session_lifetime_hours: int = 24
    secret_key: str = "development-only-change-me"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    feed_user_agent: str = "NewsIntel/0.2 (+self-hosted RSS archive)"
    feed_timeout_seconds: float = 20
    feed_max_response_bytes: int = 5_000_000
    feed_redirect_limit: int = 5
    feed_lease_seconds: int = 300
    feed_host_min_interval_seconds: float = 1
    feed_test_allowed_hosts: list[str] = []
    article_timeout_seconds: float = 20
    article_max_response_bytes: int = 5_000_000
    article_redirect_limit: int = 5
    article_lease_seconds: int = 300
    article_host_min_interval_seconds: float = 1
    article_storage_path: str = "/var/lib/newsintel/articles"
    article_temporary_html_hours: int = 24
    search_lease_seconds: int = 300
    nlp_max_input_characters: int = 1_000_000
    nlp_ner_enabled: bool = False
    nlp_ner_model: str = "en_core_web_sm"
    nlp_lease_seconds: int = 300

    @model_validator(mode="after")
    def require_secure_production_cookie(self) -> "Settings":
        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("production requires secure session cookies")
        if self.environment == "production" and len(self.secret_key) < 32:
            raise ValueError("production requires a secret key of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
