import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from test_search_timeline import _Adapter, _criteria, _Database

from app.analytics.routes import (
    ENTITY_CANDIDATES,
    TOP_N,
    ingestion_timeline,
    top_countries,
    top_entities,
)
from app.core.config import Settings
from app.main import create_app
from app.search.aggregations import date_histogram, terms
from app.search.criteria import build_query


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> type[_Adapter]:
    _Adapter.responses, _Adapter.bodies, _Adapter.unavailable = [], [], False
    monkeypatch.setattr("app.analytics.routes.ElasticsearchAdapter", _Adapter)
    return _Adapter


def _cutoff(query: dict[str, Any]) -> datetime:
    """The one discovery range the route adds on top of the shared criteria."""
    found = [
        item["range"]["first_discovered_at"]["gte"]
        for item in query["bool"]["filter"]
        if "first_discovered_at" in item.get("range", {})
    ]
    assert len(found) == 1
    return datetime.fromisoformat(found[0])


def _midnight(day: Any) -> datetime:
    return datetime.combine(day, time.min, UTC)


@pytest.mark.asyncio
async def test_ingestion_buckets_utc_discovery_days_over_the_37_day_input(
    adapter: type[_Adapter],
) -> None:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=36)
    days = [start + timedelta(days=offset) for offset in range(37)]
    adapter.responses = [
        {
            "aggregations": {
                "days": {
                    "buckets": [
                        {"key": int(_midnight(day).timestamp() * 1000), "doc_count": 1}
                        for day in days[:-1]
                    ]
                    + [{"key": int(_midnight(today).timestamp() * 1000), "doc_count": 9}]
                }
            }
        }
    ]

    # A V1 index is enough: first_discovered_at predates annotations.
    body = await ingestion_timeline(_Database(1), object(), Settings(), _criteria())  # type: ignore[arg-type]

    request = adapter.bodies[0][1]
    assert request["size"] == 0
    assert request["aggs"] == {
        "days": date_histogram("first_discovered_at", "day", _midnight(start), _midnight(today))
    }
    assert _cutoff(request["query"]) == _midnight(start)
    assert [bucket.date for bucket in body.buckets] == days[7:]
    assert body.buckets[-1].count == 9 and body.buckets[-1].is_spike
    assert not any(bucket.is_spike for bucket in body.buckets[:-1])


@pytest.mark.asyncio
async def test_ingestion_timeline_drops_publication_dates_but_keeps_other_criteria(
    adapter: type[_Adapter],
) -> None:
    adapter.responses = [{"aggregations": {"days": {"buckets": []}}}]
    criteria = _criteria("climate", start=date(2026, 1, 1))

    await ingestion_timeline(_Database(2), object(), Settings(), criteria)  # type: ignore[arg-type]

    query = adapter.bodies[0][1]["query"]
    assert not any("effective_date" in item.get("range", {}) for item in query["bool"]["filter"])
    assert query["bool"]["must"][0]["multi_match"]["query"] == "climate"


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["outage", "timed_out", "failed_shard"])
async def test_ingestion_reports_an_outage_or_partial_answer_as_unavailable(
    adapter: type[_Adapter], broken: str
) -> None:
    adapter.unavailable = broken == "outage"
    adapter.responses = [
        {"timed_out": broken == "timed_out", "_shards": {"failed": int(broken == "failed_shard")}}
    ]
    with pytest.raises(HTTPException) as exc:
        await ingestion_timeline(_Database(3), object(), Settings(), _criteria())  # type: ignore[arg-type]
    assert exc.value.status_code == 503


class _Catalogue(_Database):
    """A schema-three search target plus an Entity catalogue holding `rows`."""

    def __init__(self, rows: list[SimpleNamespace] | None = None, schema_version: int = 3) -> None:
        super().__init__(schema_version)
        self.rows = rows or []

    async def execute(self, _query: object) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self.rows)


@pytest.mark.asyncio
async def test_recent_top_countries_keep_the_rolling_30_day_discovery_cutoff(
    adapter: type[_Adapter],
) -> None:
    adapter.responses = [
        {
            "aggregations": {
                "countries": {
                    "buckets": [{"key": "GR", "doc_count": 2}, {"key": "US", "doc_count": 1}]
                }
            }
        }
    ]
    before = datetime.now(UTC)
    body = await top_countries(_Database(3), object(), Settings(), _criteria(), "recent")  # type: ignore[arg-type]

    request = adapter.bodies[0][1]
    assert request["aggs"] == {"countries": terms(None, "primary_story_country", TOP_N)}
    assert abs(_cutoff(request["query"]) - (before - timedelta(days=30))) < timedelta(seconds=5)
    assert [(item.country_code, item.count) for item in body.countries] == [("GR", 2), ("US", 1)]


@pytest.mark.asyncio
async def test_investigation_scope_is_exactly_the_shared_criteria(adapter: type[_Adapter]) -> None:
    adapter.responses = [{"aggregations": {}}]
    criteria = _criteria("grid", story_countries=["GR"], start=date(2026, 1, 1))
    await top_countries(_Database(3), object(), Settings(), criteria, "investigation")  # type: ignore[arg-type]
    assert adapter.bodies[0][1]["query"] == build_query(criteria, 3)


@pytest.mark.asyncio
async def test_top_entities_rank_only_records_of_the_requested_type(
    adapter: type[_Adapter],
) -> None:
    adapter.responses = [{"aggregations": {}}]
    criteria = _criteria(entity_types=["PERSON"])
    await top_entities(_Catalogue(), object(), Settings(), criteria, "recent")  # type: ignore[arg-type]
    assert adapter.bodies[0][1]["aggs"] == {
        "entities": terms(
            "entities",
            "entities.id",
            ENTITY_CANDIDATES,
            within={"terms": {"entities.type": ["PERSON"]}},
        )
    }


@pytest.mark.asyncio
async def test_top_entities_skip_uncatalogued_ids_and_let_the_next_rank_through(
    adapter: type[_Adapter],
) -> None:
    ghost, known = uuid.uuid4(), [uuid.uuid4() for _ in range(TOP_N + 1)]
    adapter.responses = [
        {
            "aggregations": {
                "entities": {
                    "top": {
                        "buckets": [
                            {"key": str(ghost), "doc_count": 9, "articles": {"doc_count": 9}},
                            *(
                                {"key": str(value), "doc_count": 5, "articles": {"doc_count": 3}}
                                for value in known
                            ),
                        ]
                    }
                }
            }
        }
    ]
    rows = [
        SimpleNamespace(id=value, display_text=f"E{n}", entity_type="ORG")
        for n, value in enumerate(known)
    ]
    body = await top_entities(_Catalogue(rows), object(), Settings(), _criteria(), "recent")  # type: ignore[arg-type]

    # Counts are root articles, labels come from PostgreSQL, and the ghost's slot is refilled.
    assert [(item.entity_id, item.count) for item in body.entities] == [
        (value, 3) for value in known[:TOP_N]
    ]
    assert (body.entities[0].display_text, body.entities[0].entity_type) == ("E0", "ORG")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [top_entities, top_countries])
async def test_top_views_require_a_schema_two_index(adapter: type[_Adapter], route: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await route(_Catalogue(schema_version=1), object(), Settings(), _criteria(), "recent")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "search_upgrade_required"
    assert adapter.bodies == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [top_entities, top_countries])
async def test_top_views_report_a_partial_answer_as_unavailable(
    adapter: type[_Adapter], route: Any
) -> None:
    adapter.responses = [{"timed_out": True}]
    with pytest.raises(HTTPException) as exc:
        await route(_Catalogue(), object(), Settings(), _criteria(), "recent")
    assert exc.value.status_code == 503


def test_analytics_take_the_search_criteria_and_only_top_views_take_scope() -> None:
    paths = create_app().openapi()["paths"]

    def names(path: str) -> set[str]:
        return {item["name"] for item in paths[path]["get"]["parameters"]}

    criteria = names("/api/v1/search") - {"sort", "cursor", "limit"}
    assert names("/api/v1/analytics/ingestion-timeline") == criteria
    assert names("/api/v1/analytics/top-entities") == criteria | {"scope"}
    assert names("/api/v1/analytics/top-countries") == criteria | {"scope"}
