import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from event_fixtures import annotate
from test_clustering_postgres import _article, _entity, _feed
from test_monitor_api_postgres import _client, _user
from test_monitor_evaluation_elasticsearch import _index

from app.analytics import routes
from app.core.config import get_settings
from app.db.session import session_factory
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def index_name(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"articles-v3-analytics-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object, **_: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(routes, "current_search_target", target)
    return name


async def _seed(index_name: str) -> dict[str, Any]:
    """Four articles whose discovery and publication dates sit on opposite sides of the cutoffs."""
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "15C Wire")
        ada = await _entity(db, "Ada Reyes", "PERSON")
        harbour = await _entity(db, "Harbour Authority", "ORG")
        grid = await _entity(db, "Grid Co", "ORG")
        rows = [
            # (discovered days ago, published days ago, entities, primary story country)
            (1, 90, [harbour, grid, ada], "GR"),  # recent by discovery, old by publication
            (2, 2, [ada], "GR"),
            (3, 3, [harbour], "US"),
            (40, 1, [grid], "FR"),  # old by discovery, recent by publication
        ]
        articles = []
        for discovered, published, entities, country in rows:
            article = await _article(
                db,
                feeds=[wire],
                title=f"15C {uuid.uuid4().hex}",
                title_hash=uuid.uuid4().hex,
                discovered=now - timedelta(days=discovered),
            )
            article.published_at = now - timedelta(days=published)
            await annotate(db, article.id, entities, country)
            articles.append(article)
        await db.flush()
    await _index(index_name, [article.id for article in articles])
    return {"ada": ada, "harbour": harbour, "grid": grid, "articles": articles, "now": now}


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    response = await client.get(f"/api/v1/analytics/{path}", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _pairs(items: list[dict[str, Any]], key: str) -> list[tuple[str, int]]:
    return [(item[key], item["count"]) for item in items]


async def test_recent_analytics_count_articles_by_discovery_not_publication(
    index_name: str,
) -> None:
    seeded = await _seed(index_name)
    ada, harbour, grid = (str(seeded[key].id) for key in ("ada", "harbour", "grid"))
    now = seeded["now"]

    async with _client(await _user()) as client:
        # The article discovered 40 days ago is out though published yesterday; the one
        # published 90 days ago is in, having been discovered yesterday. Ties go by id.
        entities = await _get(client, "top-entities")
        assert _pairs(entities["entities"], "entity_id") == [
            *sorted([(ada, 2), (harbour, 2)]),
            (grid, 1),
        ]
        labels = {
            e["entity_id"]: (e["display_text"], e["entity_type"]) for e in entities["entities"]
        }
        assert labels[ada] == ("Ada Reyes", "PERSON")

        # The first article matches ORG yet its PERSON entity is never ranked.
        orgs = await _get(client, "top-entities", entity_type="ORG")
        assert _pairs(orgs["entities"], "entity_id") == [(harbour, 2), (grid, 1)]

        countries = await _get(client, "top-countries")
        assert _pairs(countries["countries"], "country_code") == [("GR", 2), ("US", 1)]

        # One article per UTC discovery day; the 40-day-old one is outside the 37-day input.
        timeline = await _get(client, "ingestion-timeline")
        assert len(timeline["buckets"]) == 30
        assert timeline["buckets"][-1]["date"] == now.date().isoformat()
        assert {b["date"]: b["count"] for b in timeline["buckets"] if b["count"]} == {
            (now - timedelta(days=days)).date().isoformat(): 1 for days in (1, 2, 3)
        }
