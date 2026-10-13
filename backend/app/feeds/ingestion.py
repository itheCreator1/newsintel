import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.articles.models import Article
from app.articles.service import request_processing
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.fetching import FeedDocumentError, ParsedFeed, parse_feed_document
from app.feeds.http import FeedTooLarge, fetch_feed_http
from app.feeds.models import FETCH_FINISHED, Feed, FeedArticle, FeedFetch
from app.feeds.network import UnsafeFeedUrl
from app.feeds.normalization import normalize_article_url, normalized_title_hash
from app.feeds.scheduling import lease_is_current, next_retry_delay
from app.jobs.leasing import release
from app.nlp.service import request_article_nlp
from app.search.service import request_indexing

log = structlog.get_logger()

# A fetch stops this long before its lease runs out, to record its outcome while it still holds it.
LEASE_MARGIN_SECONDS = 2.0
# A Retry-After longer than this is read as a day.
MAX_RETRY_AFTER_SECONDS = 86_400.0
_sleep = asyncio.sleep


class TransientHttpStatus(Exception):
    def __init__(self, status_code: int, retry_after: float | None) -> None:
        super().__init__(f"Feed returned HTTP {status_code}")
        self.retry_after = retry_after


async def _persist_success(  # noqa: C901
    feed_id: uuid.UUID,
    token: str,
    parsed: ParsedFeed,
    response_status: int,
    etag: str | None,
    modified: str | None,
    duration_ms: int,
    attempt: int,
) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        feed = await db.scalar(select(Feed).where(Feed.id == feed_id).with_for_update())
        fetch = await db.scalar(
            select(FeedFetch)
            .where(FeedFetch.feed_id == feed_id, FeedFetch.claim_token == token)
            .with_for_update()
        )
        if (
            not feed
            or not fetch
            or not feed.enabled
            or feed.retired_at
            or not feed.claim_expires_at
            or not lease_is_current(token, feed.claim_token or "", feed.claim_expires_at, now)
        ):
            _abandon(fetch, now)
            return False
        new_count = 0
        invalid_count = parsed.invalid_entries
        for entry in parsed.entries:
            try:
                normalized = normalize_article_url(entry.url)
            except ValueError:
                invalid_count += 1
                continue
            article_id = await db.scalar(
                insert(Article)
                .values(
                    original_url=entry.url,
                    normalized_url=normalized,
                    title=entry.title,
                    normalized_title_hash=normalized_title_hash(entry.title),
                    published_at=entry.published_at,
                )
                .on_conflict_do_nothing(index_elements=[Article.normalized_url])
                .returning(Article.id)
            )
            if article_id is not None:
                new_count += 1
                article_input_changed = True
            else:
                article = await db.scalar(
                    select(Article).where(Article.normalized_url == normalized).with_for_update()
                )
                if article is None:
                    continue
                article_id = article.id
                article_input_changed = article.title != entry.title
                if article_input_changed:
                    article.title = entry.title
                    article.normalized_title_hash = normalized_title_hash(entry.title)
            discovery_id = await db.scalar(
                insert(FeedArticle)
                .values(
                    feed_id=feed.id,
                    article_id=article_id,
                    guid=entry.guid,
                    feed_title=entry.title,
                    feed_url=entry.url,
                    description=entry.description,
                )
                .on_conflict_do_nothing()
                .returning(FeedArticle.id)
            )
            discovery_input_changed = discovery_id is not None
            if discovery_id is None:
                discovery = await db.scalar(
                    select(FeedArticle)
                    .where(
                        FeedArticle.feed_id == feed.id,
                        FeedArticle.article_id == article_id,
                    )
                    .with_for_update()
                )
                if discovery is not None and (
                    discovery.feed_title != entry.title
                    or discovery.description != entry.description
                ):
                    discovery.feed_title = entry.title
                    discovery.description = entry.description
                    discovery_input_changed = True
            if discovery_id is not None and feed.fetching_mode != "rss":
                try:
                    await request_processing(db, article_id, feed.fetching_mode, automatic=True)
                except ValueError:
                    pass
            if discovery_input_changed or article_input_changed:
                await request_article_nlp(db, article_id)
                await request_indexing(db, article_id)
        fetch.status = "success"
        fetch.attempt_count = attempt
        fetch.http_status = response_status
        fetch.duration_ms = duration_ms
        fetch.entry_count = len(parsed.entries)
        fetch.invalid_entry_count = invalid_count
        fetch.new_article_count = new_count
        fetch.etag = etag
        fetch.last_modified = modified
        fetch.completed_at = now
        if response_status != 304:
            feed.etag = etag
            feed.last_modified = modified
        feed.last_success_at = now
        feed.next_poll_at = now + timedelta(minutes=feed.poll_interval_minutes)
        release(feed)
    return True


def _abandon(fetch: FeedFetch | None, now: datetime) -> None:
    """End a fetch whose claim was lost (feed retired, disabled or reclaimed) before it finished;
    a finished one, as a redelivered message finds, is left alone.
    """
    if fetch is None or fetch.status in FETCH_FINISHED:
        return
    fetch.status = "failed"
    fetch.error_category = "abandoned"
    fetch.completed_at = now


async def _finish_unchanged(feed_id: uuid.UUID, token: str, duration_ms: int, attempt: int) -> bool:
    return await _persist_success(
        feed_id, token, ParsedFeed(None, [], 0), 304, None, None, duration_ms, attempt
    )


async def _finish_failure(
    feed_id: uuid.UUID,
    token: str,
    category: str,
    message: str,
    attempt: int,
    *,
    retry_at: datetime | None = None,
) -> None:
    """Fail the fetch and release the feed: it is polled again after its interval, or at
    `retry_at` when the server said when to come back."""
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        feed = await db.scalar(select(Feed).where(Feed.id == feed_id).with_for_update())
        fetch = await db.scalar(
            select(FeedFetch)
            .where(FeedFetch.feed_id == feed_id, FeedFetch.claim_token == token)
            .with_for_update()
        )
        if not feed or not fetch or feed.claim_token != token:
            return
        fetch.status = "failed"
        fetch.attempt_count = attempt
        fetch.error_category = category
        fetch.error_message = message[:1000]
        fetch.completed_at = now
        feed.next_poll_at = retry_at or now + timedelta(minutes=feed.poll_interval_minutes)
        release(feed)


def _category(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, UnsafeFeedUrl):
        return "security", False
    if isinstance(exc, FeedDocumentError):
        return "malformed_feed", False
    if isinstance(exc, FeedTooLarge):
        return "response_too_large", False
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout", True
    if isinstance(exc, TransientHttpStatus):
        return "http_transient", True
    if isinstance(exc, httpx.HTTPError):
        return "network", True
    return "internal", False


async def ingest_claim(feed_id: uuid.UUID, token: str) -> None:  # noqa: C901
    settings = get_settings()
    last_error: Exception | None = None
    for attempt in range(1, 4):
        async with session_factory() as db:
            feed = await db.get(Feed, feed_id)
            if not feed or not feed.enabled or feed.retired_at or feed.claim_token != token:
                _abandon(
                    await db.scalar(
                        select(FeedFetch)
                        .where(FeedFetch.feed_id == feed_id, FeedFetch.claim_token == token)
                        .with_for_update()
                    ),
                    datetime.now(UTC),
                )
                await db.commit()
                return
            url, etag, modified = feed.url, feed.etag, feed.last_modified
            lease_ends = feed.claim_expires_at
        started = datetime.now(UTC)
        # Every attempt ends before the lease does; past it, another worker may claim the feed.
        deadline = lease_ends - timedelta(seconds=LEASE_MARGIN_SECONDS) if lease_ends else started
        try:
            validators = {}
            if etag:
                validators["If-None-Match"] = etag
            if modified:
                validators["If-Modified-Since"] = modified
            remaining = (deadline - started).total_seconds()
            if remaining <= 0:
                raise TimeoutError("No time left in the fetch's lease")
            response = await asyncio.wait_for(
                fetch_feed_http(url, validators, settings), timeout=remaining
            )
            duration = int((datetime.now(UTC) - started).total_seconds() * 1000)
            if response.status_code == 304:
                await _finish_unchanged(feed_id, token, duration, attempt)
                return
            if response.status_code == 429 or response.status_code >= 500:
                retry_header = response.headers.get("retry-after")
                try:
                    retry_after = float(retry_header) if retry_header else None
                except ValueError:
                    retry_after = None
                raise TransientHttpStatus(response.status_code, retry_after)
            if response.status_code >= 400:
                raise FeedDocumentError(f"Feed returned HTTP {response.status_code}")
            parsed = parse_feed_document(response.content, response.url)
            await _persist_success(
                feed_id,
                token,
                parsed,
                response.status_code,
                response.headers.get("etag"),
                response.headers.get("last-modified"),
                duration,
                attempt,
            )
            return
        except Exception as exc:
            last_error = exc
            category, transient = _category(exc)
            log.warning(
                "feed_fetch_failed",
                feed_id=str(feed_id),
                fetch_token=token,
                attempt=attempt,
                error_category=category,
            )
            if not transient or attempt == 3:
                await _finish_failure(feed_id, token, category, str(exc), attempt)
                return
            retry_after = (
                last_error.retry_after if isinstance(last_error, TransientHttpStatus) else None
            )
            delay = next_retry_delay(attempt, retry_after=retry_after, jitter=random.random())
            now = datetime.now(UTC)
            if now + timedelta(seconds=delay + settings.feed_timeout_seconds) > deadline:
                # The wait and another attempt would outlive the lease: come back later instead.
                wait = delay if retry_after is None else min(retry_after, MAX_RETRY_AFTER_SECONDS)
                retry_at = now + timedelta(seconds=wait)
                await _finish_failure(
                    feed_id, token, category, str(exc), attempt, retry_at=retry_at
                )
                return
            await _sleep(delay)
    if last_error:
        await _finish_failure(feed_id, token, "internal", str(last_error), 3)
