import asyncio
import socket
from datetime import UTC, datetime, timedelta

import pytest

from app.feeds.fetching import FeedDocumentError, parse_feed_document
from app.feeds.network import UnsafeFeedUrl, validate_public_url
from app.feeds.normalization import normalize_article_url, normalized_title_hash
from app.feeds.scheduling import lease_is_current, next_retry_delay


def test_tracking_variants_share_one_canonical_url() -> None:
    assert (
        normalize_article_url(
            "HTTPS://Example.COM:443/story?id=7&utm_source=rss&fbclid=nope#comments"
        )
        == "https://example.com/story?id=7"
    )


def test_meaningful_query_parameters_and_nondefault_ports_are_preserved() -> None:
    assert normalize_article_url("http://EXAMPLE.com:8080/a?b=2&a=1") == (
        "http://example.com:8080/a?a=1&b=2"
    )


def test_title_hash_normalizes_case_and_spacing_without_merging_urls() -> None:
    assert normalized_title_hash("  A   Big Story ") == normalized_title_hash("a big story")


def test_rss_and_atom_entries_are_parsed_with_relative_links_and_plain_text() -> None:
    rss = b"""<?xml version='1.0'?><rss version='2.0'><channel><title>Wire</title>
      <link>https://example.com/news/</link><item><guid>one</guid><title>First</title>
      <link>story?id=1</link><description><![CDATA[<p>Hello <b>world</b></p>]]></description>
      <pubDate>Sun, 13 Sep 2026 09:30:00 GMT</pubDate></item>
      <item><title>Broken</title></item></channel></rss>"""
    parsed = parse_feed_document(rss, "https://example.com/feed.xml")
    assert len(parsed.entries) == 1
    assert parsed.invalid_entries == 1
    assert parsed.entries[0].url == "https://example.com/news/story?id=1"
    assert parsed.entries[0].description == "Hello world"
    assert parsed.entries[0].published_at == datetime(2026, 9, 13, 9, 30, tzinfo=UTC)

    atom = b"""<feed xmlns='http://www.w3.org/2005/Atom'><title>Atom</title>
      <link rel='alternate' href='https://example.net/'/>
      <entry><id>tag:example.net,2026:1</id><title>Second</title>
      <link href='/second'/><summary>Text</summary></entry></feed>"""
    assert parse_feed_document(atom, "https://example.net/atom").entries[0].url == (
        "https://example.net/second"
    )


def test_malformed_or_non_feed_xml_is_rejected() -> None:
    with pytest.raises(FeedDocumentError):
        parse_feed_document(b"<rss><broken>", "https://example.com/feed")
    with pytest.raises(FeedDocumentError):
        parse_feed_document(b"<html><body>no feed</body></html>", "https://example.com/feed")


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com/rss",
        "http://127.0.0.1/rss",
        "http://[::1]/rss",
        "file:///etc/passwd",
    ],
)
@pytest.mark.asyncio
async def test_private_or_credentialed_feed_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeFeedUrl):
        await validate_public_url(url)


@pytest.mark.asyncio
async def test_ipv4_addresses_are_pinned_before_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    # A plain string sort put "2a02:..." before "95.100..." and pinned an IPv6 address the
    # Docker network cannot route, so dual-stack feeds failed whenever DNS rotated.
    async def records(*_args: object, **_kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2a02:26f0::2d63", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("95.100.237.185", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", records)
    destination = await validate_public_url("https://rss.dw.com/rss")
    assert destination.addresses == ("95.100.237.185", "2a02:26f0::2d63")


def test_stale_claims_are_rejected_and_retry_is_bounded() -> None:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    assert lease_is_current("claim", "claim", now + timedelta(seconds=1), now)
    assert not lease_is_current("old", "claim", now + timedelta(seconds=1), now)
    assert not lease_is_current("claim", "claim", now, now)
    assert next_retry_delay(1, jitter=0) == 2
    assert next_retry_delay(3, retry_after=9999, jitter=0) == 300
