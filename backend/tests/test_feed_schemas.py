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


def test_feed_tags_are_trimmed_and_deduplicated_ignoring_case() -> None:
    feed = FeedCreate(
        name="Wire",
        url="https://example.com/rss",
        tags=["  Ελληνικά ", "economy", "Economy", "", "world   news"],
    )
    assert feed.tags == ["Ελληνικά", "economy", "world news"]
    assert FeedUpdate(tags=[" a ", "A"]).tags == ["a"]
    assert FeedUpdate().tags is None


def test_feed_tag_has_a_length_limit() -> None:
    with pytest.raises(ValidationError):
        FeedCreate(name="Wire", url="https://example.com/rss", tags=["x" * 41])


def test_feed_country_and_language_codes_are_normalised() -> None:
    feed = FeedCreate(
        name="Wire", url="https://example.com/rss", source_country="gr", expected_language="EL"
    )
    assert (feed.source_country, feed.expected_language) == ("GR", "el")
    update = FeedUpdate(source_country="de", expected_language="De")
    assert (update.source_country, update.expected_language) == ("DE", "de")
    assert FeedUpdate(source_country=None).source_country is None
