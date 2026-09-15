from datetime import UTC, datetime, time, timedelta
from typing import Literal

from app.search.criteria import SearchCriteria

Interval = Literal["hour", "day", "week", "month", "year"]
RequestedInterval = Literal["auto", "hour", "day", "week", "month", "year"]
INTERVALS: tuple[Interval, ...] = ("hour", "day", "week", "month", "year")
MAX_BUCKETS = 200


class TimelineTooFine(ValueError):
    pass


def _floor(interval: Interval, value: datetime) -> datetime:
    value = value.astimezone(UTC)
    if interval == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    day = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "day":
        return day
    if interval == "week":
        return day - timedelta(days=day.weekday())
    if interval == "month":
        return day.replace(day=1)
    return day.replace(month=1, day=1)


def bucket_count(interval: Interval, first: datetime, last: datetime) -> int:
    start, end = _floor(interval, first), _floor(interval, last)
    if interval == "month":
        return (end.year - start.year) * 12 + end.month - start.month + 1
    if interval == "year":
        return end.year - start.year + 1
    step = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}
    return (end - start) // step[interval] + 1


def select_interval(requested: RequestedInterval, first: datetime, last: datetime) -> Interval:
    if requested != "auto":
        if bucket_count(requested, first, last) > MAX_BUCKETS:
            raise TimelineTooFine(
                f"{requested.capitalize()} buckets for this range would exceed {MAX_BUCKETS}"
            )
        return requested
    for interval in INTERVALS:
        if bucket_count(interval, first, last) <= MAX_BUCKETS:
            return interval
    return "year"


def histogram_bounds(
    criteria: SearchCriteria, data_min: datetime, data_max: datetime
) -> tuple[datetime, datetime]:
    first = datetime.combine(criteria.start, time.min, UTC) if criteria.start else data_min
    last = (
        datetime.combine(criteria.end, time.min, UTC) - timedelta(milliseconds=1)
        if criteria.end
        else data_max
    )
    return first, max(first, last)
