"""Every page that reads Elasticsearch tells a refused request apart from an outage (fix plan Δ1).

A query Elasticsearch rejects is a 502 with the code `search_rejected`, which a retry will not fix;
only an Elasticsearch that could not answer is the 503 "unavailable".
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import update
from test_clustering_postgres import _article, _entity, _feed
from test_monitor_api_postgres import CSRF, _client, _user

from app.db.session import session_factory
from app.monitors.models import Monitor
from app.search.elasticsearch import (
    ElasticsearchAdapter,
    ElasticsearchRejected,
    ElasticsearchUnavailable,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

# Every module that resolves the live index for a page that reads Elasticsearch.
TARGET_READERS = [
    "app.search.routes",
    "app.search.related",
    "app.graph.routes",
    "app.analytics.routes",
    "app.geo.investigation",
    "app.monitors.results",
    "app.monitors.changes",
]

PAGES = [
    "/api/v1/search?q=grid",
    "/api/v1/search/export?q=grid",
    "/api/v1/search/timeline?q=grid",
    "/api/v1/search/facets?q=grid",
    "/api/v1/articles/{article}/related",
    "/api/v1/graph/entities?q=grid",
    "/api/v1/graph/edges/evidence?q=grid&source={source}&target={target}",
    "/api/v1/analytics/ingestion-timeline?q=grid",
    "/api/v1/analytics/top-entities?q=grid&scope=investigation",
    "/api/v1/analytics/top-countries?q=grid&scope=investigation",
    "/api/v1/analytics/rising-entities?q=grid",
    "/api/v1/geo/countries?q=grid&role=story&scope=investigation",
    "/api/v1/geo/articles?q=grid&role=story&code=GR&scope=investigation",
    "/api/v1/monitors/{monitor}/results",
    "/api/v1/monitors/{monitor}/changes",
]


@pytest_asyncio.fixture(loop_scope="session")
async def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A user, an article, two entities and a watch, with a live index every reader resolves."""

    async def target(_db: object) -> str:
        return "articles-test"

    for module in TARGET_READERS:
        monkeypatch.setattr(f"{module}.current_search_target", target)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Failure Wire")
        article = await _article(db, feeds=[wire], title="Grid", title_hash=uuid.uuid4().hex)
        source, other = await _entity(db, "Grid Operator"), await _entity(db, "Regulator")
    user = await _user()
    async with _client(user) as client:
        created = await client.post(
            "/api/v1/monitors",
            json={"name": f"grid {uuid.uuid4().hex}", "kind": "search", "state": {"q": "grid"}},
            headers=CSRF,
        )
    assert created.status_code == 201, created.text
    # A watch that has been evaluated and viewed once, so both of its pages read a window.
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        await db.execute(
            update(Monitor)
            .where(Monitor.id == uuid.UUID(created.json()["id"]))
            .values(viewed_cursor_at=now - timedelta(hours=1), eval_cursor_at=now)
        )
    return SimpleNamespace(
        user=user,
        ids={
            "article": article.id,
            "source": source.id,
            "target": other.id,
            "monitor": created.json()["id"],
        },
    )


def _failing(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    async def fail(*_args: object, **_kwargs: object) -> httpx.Response:
        raise failure

    monkeypatch.setattr(ElasticsearchAdapter, "_request", fail)


@pytest.mark.parametrize("page", PAGES)
async def test_a_rejected_query_is_a_502_with_its_code(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, page: str
) -> None:
    _failing(monkeypatch, ElasticsearchRejected("parsing_exception", 400))
    async with _client(world.user) as client:
        response = await client.get(page.format(**world.ids))
    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "search_rejected"


@pytest.mark.parametrize("page", PAGES)
async def test_an_elasticsearch_that_cannot_answer_is_a_503(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, page: str
) -> None:
    _failing(monkeypatch, ElasticsearchUnavailable("connection refused"))
    async with _client(world.user) as client:
        response = await client.get(page.format(**world.ids))
    assert response.status_code == 503, response.text
