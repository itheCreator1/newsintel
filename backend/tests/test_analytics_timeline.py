from datetime import date, timedelta

from app.analytics.timeline import daily_counts, detect_spikes


def _dates(start: date, count: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


def test_daily_counts_zero_fills_days_with_no_articles() -> None:
    start = date(2026, 9, 1)
    counts_by_day = {date(2026, 9, 1): 3, date(2026, 9, 3): 5}

    counts = daily_counts(counts_by_day, start, days=4)

    assert counts == [3, 0, 5, 0]


def test_detect_spikes_flags_a_day_at_or_above_twice_the_trailing_average() -> None:
    # Trailing 7-day average of the first seven 2s is 2; a day of 4 is exactly 2x -> spike.
    counts = [2] * 7 + [4]

    spikes = detect_spikes(counts)

    assert spikes == [False] * 7 + [True]


def test_detect_spikes_does_not_flag_a_day_just_under_the_threshold() -> None:
    counts = [2] * 7 + [3]

    spikes = detect_spikes(counts)

    assert spikes[-1] is False


def test_detect_spikes_only_looks_at_the_trailing_window_not_the_whole_history() -> None:
    # A huge day-0 spike should stop affecting the average once it has fully scrolled out
    # of a 3-day trailing window, so later steady days are not falsely flagged.
    counts = [9, 1, 1, 1, 1, 1, 1]

    spikes = detect_spikes(counts, window=3)

    assert spikes == [False] * 7


def test_detect_spikes_never_flags_a_day_with_zero_articles() -> None:
    counts = [0, 0, 0]

    spikes = detect_spikes(counts)

    assert spikes == [False, False, False]


def test_detect_spikes_flags_the_first_real_day_after_a_zero_baseline() -> None:
    # Trailing average is 0 (no prior articles at all); any positive count is a spike from nothing.
    counts = [0] * 7 + [1]

    spikes = detect_spikes(counts)

    assert spikes[-1] is True


def test_detect_spikes_treats_a_missing_trailing_window_as_no_spike() -> None:
    # There isn't a full trailing window of history yet, regardless of how high the count is.
    spikes = detect_spikes([500], window=7)

    assert spikes == [False]
