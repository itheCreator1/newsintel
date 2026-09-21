from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from test_search_timeline import _Adapter, _criteria, _Database

from app.analytics.routes import ingestion_timeline
from app.core.config import Settings
from app.search.aggregations import date_histogram


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
