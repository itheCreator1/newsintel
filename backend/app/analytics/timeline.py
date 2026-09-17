from datetime import date, timedelta

DISPLAY_DAYS = 30
TRAILING_WINDOW = 7
LOOKBACK_DAYS = DISPLAY_DAYS + TRAILING_WINDOW
SPIKE_MULTIPLIER = 2.0


def daily_counts(counts_by_day: dict[date, int], start: date, days: int) -> list[int]:
    return [counts_by_day.get(start + timedelta(days=offset), 0) for offset in range(days)]


def detect_spikes(
    counts: list[int], window: int = TRAILING_WINDOW, multiplier: float = SPIKE_MULTIPLIER
) -> list[bool]:
    spikes = []
    for index, count in enumerate(counts):
        if index < window:
            spikes.append(False)
            continue
        average = sum(counts[index - window : index]) / window
        spikes.append(count > 0 and count >= multiplier * average)
    return spikes
