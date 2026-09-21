import pytest
from pydantic import ValidationError

from app.feeds.schemas import FeedCreate, FeedUpdate


def test_new_feed_defaults_to_enabled_rss_every_hour() -> None:
    feed = FeedCreate(name="Wire", url="https://example.com/rss")
    assert feed.enabled is True
    assert feed.fetching_mode == "rss"
    assert feed.poll_interval_minutes == 60


def test_feed_interval_has_five_minute_floor() -> None:
    with pytest.raises(ValidationError):
        FeedCreate(name="Wire", url="https://example.com/rss", poll_interval_minutes=4)


@pytest.mark.parametrize("mode", ["rss", "full_text", "full_text_html"])
def test_feed_accepts_all_collection_modes(mode: str) -> None:
    feed = FeedCreate(name="Wire", url="https://example.com/rss", fetching_mode=mode)  # type: ignore[arg-type]
    assert feed.fetching_mode == mode


def test_feed_rejects_unknown_collection_mode() -> None:
    with pytest.raises(ValidationError):
        FeedUpdate(fetching_mode="full")  # type: ignore[arg-type]
