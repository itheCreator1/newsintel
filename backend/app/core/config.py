from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NEWSINTEL_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel"
    database_pool_size: int = 10
    database_pool_overflow: int = 10
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
    # The Greek model, used once Greek entities are switched on in Settings.
    nlp_ner_model_el: str = "el_core_news_sm"
    nlp_lease_seconds: int = 300
    clustering_lease_seconds: int = 300
    monitor_interval_seconds: int = 300
    monitor_retry_seconds: int = 300
    monitor_lease_seconds: int = 120
    monitor_settle_seconds: int = 120
    # Wikidata (authority control, phase 3). Nothing is sent without a contact (an email or a
    # URL), which Wikimedia's User-Agent policy requires; wikidata_enabled=false stops all traffic.
    wikidata_enabled: bool = True
    wikidata_url: str = "https://www.wikidata.org/w/api.php"
    wikidata_contact: str = ""
    wikidata_languages: list[str] = ["el", "en"]
    wikidata_timeout_seconds: float = 20
    wikidata_max_response_bytes: int = 20_000_000
    # A full fetch (with claims) keeps the summed page sizes of one request under this.
    wikidata_batch_bytes: int = 2_000_000
    # Far inside Wikimedia's limits (200 a minute with a proper User-Agent): one request at a
    # time for the whole app, at most one every 3 s, and 5 s after an answer that took over 1 s.
    wikidata_min_interval_seconds: float = 3
    wikidata_slow_response_seconds: float = 1
    wikidata_slow_wait_seconds: float = 5
    wikidata_daily_request_budget: int = 2000
    # A 429 or 503 stops all traffic for max(Retry-After, this); a second within the hour for a day.
    wikidata_rate_limit_pause_minutes: float = 15
    wikidata_repeat_rate_limit_pause_hours: float = 24
    # maxlag, other errors and timeouts: max(Retry-After, this), doubling up to the maximum.
    wikidata_error_pause_minutes: float = 5
    wikidata_error_pause_max_hours: float = 6
    # A 403 may mean we are blocked: stop for a day and say so.
    wikidata_blocked_pause_hours: float = 24
    wikidata_refresh_days: int = 30
    wikidata_search_cache_days: int = 30
    wikidata_class_cache_days: int = 180
    wikidata_candidate_min_articles: int = 3
    # A sweep for candidates starts once a day; a worker holds a run for one batch at a time.
    wikidata_candidate_sweep_hours: float = 24
    wikidata_run_batch_seconds: float = 120
    wikidata_run_lease_seconds: float = 900

    @model_validator(mode="after")
    def require_secure_production_cookie(self) -> "Settings":
        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("production requires secure session cookies")
        if self.environment == "production" and len(self.secret_key) < 32:
            raise ValueError("production requires a secret key of at least 32 characters")
        return self

    @model_validator(mode="after")
    def keep_wikidata_gentle(self) -> "Settings":
        if self.wikidata_min_interval_seconds < 1:
            raise ValueError("wikidata_min_interval_seconds may not be under 1 second")
        if self.wikidata_slow_wait_seconds < 5:
            raise ValueError("wikidata_slow_wait_seconds may not be under 5 seconds")
        if self.wikidata_rate_limit_pause_minutes < 15:
            raise ValueError("wikidata_rate_limit_pause_minutes may not be under 15 minutes")
        if self.wikidata_error_pause_minutes < 1:
            raise ValueError("wikidata_error_pause_minutes may not be under 1 minute")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
