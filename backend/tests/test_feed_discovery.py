from datetime import UTC, datetime

import httpx
import pytest

from app.feeds.discovery import FeedProbeError, feed_links, probe_feed
from app.feeds.http import FeedHttpResponse

GREEK_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Η Καθημερινή</title><link>https://news.example/</link>
<item><title>Η κυβέρνηση ανακοίνωσε νέα μέτρα για την ενέργεια και τα νοικοκυριά</title>
<link>/a</link><pubDate>Mon, 12 Oct 2026 08:00:00 +0000</pubDate>
<description>Τα μέτρα αφορούν τους λογαριασμούς ρεύματος
και φυσικού αερίου του χειμώνα.</description></item>
<item><title>Η Βουλή ψηφίζει σήμερα τον προϋπολογισμό του επόμενου έτους</title>
<link>/b</link><pubDate>Sun, 11 Oct 2026 08:00:00 +0000</pubDate>
<description>Η συζήτηση στην Ολομέλεια ολοκληρώνεται αργά το βράδυ
με ονομαστική ψηφοφορία.</description></item>
</channel></rss>""".encode()

PAGE = b"""<!doctype html><html><head><title>News</title>
<link rel="stylesheet" href="/style.css">
<link rel="alternate" type="application/rss+xml" title="All news" href="/rss.xml">
<link rel="alternate" type="application/atom+xml" title="Sport" href="https://news.example/sport.atom">
<link rel="alternate" hreflang="en" href="/en/">
</head><body></body></html>"""


def _fetcher(pages: dict[str, tuple[int, bytes]]):  # type: ignore[no-untyped-def]
    seen: list[str] = []

    async def fetch(url: str) -> FeedHttpResponse:
        seen.append(url)
        status, content = pages[url]
        return FeedHttpResponse(status, content, httpx.Headers(), url)

    return fetch, seen


def test_a_page_lists_its_rss_and_atom_feeds() -> None:
    assert feed_links(PAGE, "https://news.example/home") == [
        ("https://news.example/rss.xml", "All news"),
        ("https://news.example/sport.atom", "Sport"),
    ]


@pytest.mark.asyncio
async def test_a_feed_reports_its_title_entries_language_and_latest_entry() -> None:
    fetch, _ = _fetcher({"https://news.example/rss": (200, GREEK_RSS)})
    probe = await probe_feed("https://news.example/rss", fetch)
    assert probe.url == "https://news.example/rss"
    assert probe.title == "Η Καθημερινή"
    assert probe.entry_count == 2
    assert probe.language == "el"
    assert probe.latest_entry_at == datetime(2026, 10, 12, 8, tzinfo=UTC)
    assert probe.discovered_from is None


@pytest.mark.asyncio
async def test_a_site_address_finds_the_feed_it_links() -> None:
    fetch, seen = _fetcher(
        {
            "https://news.example/home": (200, PAGE),
            "https://news.example/rss.xml": (200, GREEK_RSS),
        }
    )
    probe = await probe_feed("https://news.example/home", fetch)
    assert seen == ["https://news.example/home", "https://news.example/rss.xml"]
    assert probe.url == "https://news.example/rss.xml"
    assert probe.discovered_from == "https://news.example/home"
    assert probe.candidates == [
        ("https://news.example/rss.xml", "All news"),
        ("https://news.example/sport.atom", "Sport"),
    ]


@pytest.mark.asyncio
async def test_too_little_text_leaves_the_language_open() -> None:
    feed = b"<rss><channel><title>T</title><item><title>Hi</title><link>https://x.example/a</link></item></channel></rss>"
    fetch, _ = _fetcher({"https://x.example/rss": (200, feed)})
    assert (await probe_feed("https://x.example/rss", fetch)).language is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ({"https://x.example/": (404, b"")}, "answered HTTP 404"),
        ({"https://x.example/": (200, b"<html><head></head></html>")}, "links to no feed"),
        (
            {
                "https://x.example/": (
                    200,
                    b'<link rel="alternate" type="application/rss+xml" href="/f">',
                ),
                "https://x.example/f": (200, b"<html></html>"),
            },
            "is not RSS or Atom",
        ),
    ],
)
async def test_what_went_wrong_is_said_plainly(
    pages: dict[str, tuple[int, bytes]], message: str
) -> None:
    fetch, _ = _fetcher(pages)
    with pytest.raises(FeedProbeError, match=message):
        await probe_feed("https://x.example/", fetch)
