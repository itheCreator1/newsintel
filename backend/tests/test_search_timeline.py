from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.main import create_app
from app.search.criteria import SearchCriteria, build_query
from app.search.elasticsearch import ElasticsearchUnavailable
from app.search.query import parse_query
from app.search.routes import search_timeline
from app.search.schemas import SearchTimeline
from app.search.timeline import (
    MAX_BUCKETS,
    TimelineTooFine,
    bucket_count,
    histogram_bounds,
    select_interval,
)


def _criteria(q: str = "", **overrides: object) -> SearchCriteria:
    values: dict[str, object] = {
        "parsed": parse_query(q),
        "q": q,
        "sources": [],
        "countries": [],
        "start": None,
        "end": None,
        "content_available": None,
        "processing": [],
        "languages": [],
        "entity_ids": [],
        "entity_types": [],
        "keyword_ids": [],
        "story_countries": [],
        "mentioned_countries": [],
    }
    values.update(overrides)
    return SearchCriteria(**values)  # type: ignore[arg-type]


def test_build_query_keeps_source_and_country_on_one_provenance_entry() -> None:
    query = build_query(
        _criteria("climate", sources=["s1"], countries=["GR"], start=date(2026, 1, 1)),
        schema_version=1,
    )

    must, filters = query["bool"]["must"], query["bool"]["filter"]
    assert must[0]["multi_match"]["fields"] == ["title^3", "descriptions", "body"]
    assert filters[0]["nested"]["query"]["bool"]["must"] == [
        {"terms": {"provenance.source_id": ["s1"]}},
        {"terms": {"provenance.source_country": ["GR"]}},
    ]
    assert filters[1] == {"range": {"effective_date": {"gte": "2026-01-01T00:00:00+00:00"}}}


def test_build_query_matches_unresolved_annotation_names_to_nothing() -> None:
    query = build_query(_criteria('entity:"Nobody"'), schema_version=2)

    assert query["bool"]["filter"] == [
        {
            "nested": {
                "path": "entities",
                "query": {"bool": {"must": [{"terms": {"entities.id": []}}]}},
            }
        }
    ]
    assert query["bool"]["must"] == [{"match_all": {}}]


@pytest.mark.parametrize(
    ("interval", "first", "last", "expected"),
    [
        (
            "hour",
            datetime(2026, 1, 1, 0, 59, tzinfo=UTC),
            datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
            3,
        ),
        ("day", datetime(2026, 1, 1, 23, tzinfo=UTC), datetime(2026, 1, 3, 0, tzinfo=UTC), 3),
        ("week", datetime(2026, 1, 4, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC), 2),
        ("month", datetime(2025, 12, 31, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC), 3),
        ("year", datetime(2020, 6, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), 7),
    ],
)
def test_bucket_count_aligns_to_calendar_boundaries(
    interval: str, first: datetime, last: datetime, expected: int
) -> None:
    assert bucket_count(interval, first, last) == expected  # type: ignore[arg-type]


def test_auto_interval_picks_the_finest_bounded_calendar_unit() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert select_interval("auto", start, datetime(2026, 1, 2, tzinfo=UTC)) == "hour"
    assert select_interval("auto", start, datetime(2026, 3, 1, tzinfo=UTC)) == "day"
    assert select_interval("auto", start, datetime(2027, 1, 1, tzinfo=UTC)) == "week"
    assert select_interval("auto", start, datetime(2030, 1, 1, tzinfo=UTC)) == "month"
    assert select_interval("auto", datetime(1990, 1, 1, tzinfo=UTC), start) == "year"


def test_manual_interval_is_rejected_when_it_exceeds_the_bucket_limit() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert select_interval("day", start, datetime(2026, 1, 5, tzinfo=UTC)) == "day"
    with pytest.raises(TimelineTooFine):
        select_interval("hour", start, datetime(2026, 12, 31, tzinfo=UTC))
    assert MAX_BUCKETS == 200


def test_histogram_bounds_prefer_requested_dates_over_data_extent() -> None:
    data_min = datetime(2026, 1, 10, tzinfo=UTC)
    data_max = datetime(2026, 1, 20, tzinfo=UTC)

    assert histogram_bounds(_criteria(), data_min, data_max) == (data_min, data_max)
    first, last = histogram_bounds(
        _criteria(start=date(2026, 1, 1), end=date(2026, 2, 1)), data_min, data_max
    )
    assert first == datetime(2026, 1, 1, tzinfo=UTC)
    assert last == datetime(2026, 1, 31, 23, 59, 59, 999000, tzinfo=UTC)


class _Target:
    index_name = "articles-v2-test"

    def __init__(self, schema_version: int) -> None:
        self.schema_version = schema_version


class _Database:
    def __init__(self, schema_version: int = 2) -> None:
        self.target = _Target(schema_version)

    async def scalar(self, _query: object) -> _Target:
        return self.target


class _Adapter:
    responses: list[dict[str, Any]] = []
    bodies: list[tuple[str, dict[str, Any]]] = []
    unavailable = False

    def __init__(self, _url: str) -> None:
        pass

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        if _Adapter.unavailable:
            raise ElasticsearchUnavailable("connection refused")
        _Adapter.bodies.append((index_name, body))
        return _Adapter.responses.pop(0)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> type[_Adapter]:
    _Adapter.responses, _Adapter.bodies, _Adapter.unavailable = [], [], False
    monkeypatch.setattr("app.search.routes.ElasticsearchAdapter", _Adapter)
    return _Adapter


async def _timeline(
    criteria: SearchCriteria, interval: str = "auto", schema_version: int = 2
) -> SearchTimeline:
    return await search_timeline(
        _Database(schema_version),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Settings(),
        criteria,
        interval,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_timeline_buckets_the_same_filtered_query(adapter: type[_Adapter]) -> None:
    first, last = datetime(2026, 1, 1, 8, tzinfo=UTC), datetime(2026, 1, 20, tzinfo=UTC)
    adapter.responses = [
        {"aggregations": {"first": {"value": _ms(first)}, "last": {"value": _ms(last)}}},
        {
            "aggregations": {
                "timeline": {
                    "buckets": [
                        {"key": _ms(datetime(2026, 1, 1, tzinfo=UTC)), "doc_count": 4},
                        {"key": _ms(datetime(2026, 1, 2, tzinfo=UTC)), "doc_count": 0},
                    ]
                }
            }
        },
    ]
    criteria = _criteria("climate", languages=["en"])

    timeline = await _timeline(criteria)

    assert timeline.interval == "day"
    assert timeline.total == 4
    assert [bucket.count for bucket in timeline.buckets] == [4, 0]
    (extent_index, extent), (histogram_index, histogram) = adapter.bodies
    assert extent_index == histogram_index == "articles-v2-test"
    assert extent["query"] == histogram["query"] == build_query(criteria, 2)
    settings = histogram["aggs"]["timeline"]["date_histogram"]
    assert settings["calendar_interval"] == "day"
    assert settings["extended_bounds"] == {"min": _ms(first), "max": _ms(last)}


@pytest.mark.asyncio
async def test_timeline_without_matches_skips_the_histogram(adapter: type[_Adapter]) -> None:
    adapter.responses = [{"aggregations": {"first": {"value": None}, "last": {"value": None}}}]

    timeline = await _timeline(_criteria(), interval="week")

    assert (timeline.interval, timeline.total, timeline.buckets) == ("week", 0, [])
    assert len(adapter.bodies) == 1


@pytest.mark.asyncio
async def test_timeline_rejects_a_manual_interval_that_is_too_fine(
    adapter: type[_Adapter],
) -> None:
    adapter.responses = [
        {
            "aggregations": {
                "first": {"value": _ms(datetime(2025, 1, 1, tzinfo=UTC))},
                "last": {"value": _ms(datetime(2026, 1, 1, tzinfo=UTC))},
            }
        }
    ]

    with pytest.raises(HTTPException) as exc:
        await _timeline(_criteria(), interval="hour")

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "timeline_too_fine"  # type: ignore[index]


@pytest.mark.asyncio
async def test_timeline_requires_schema_two_for_annotation_filters(
    adapter: type[_Adapter],
) -> None:
    with pytest.raises(HTTPException) as exc:
        await _timeline(_criteria(keyword_ids=["k1"]), schema_version=1)

    assert exc.value.status_code == 409
    assert adapter.bodies == []


@pytest.mark.asyncio
async def test_timeline_reports_elasticsearch_outage(adapter: type[_Adapter]) -> None:
    adapter.unavailable = True

    with pytest.raises(HTTPException) as exc:
        await _timeline(_criteria())

    assert exc.value.status_code == 503


def test_timeline_route_shares_search_parameters_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    names = {item["name"] for item in paths["/api/v1/search/timeline"]["get"]["parameters"]}
    search_names = {item["name"] for item in paths["/api/v1/search"]["get"]["parameters"]}
    assert search_names - {"sort", "limit", "cursor"} | {"interval"} == names
