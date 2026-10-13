"""Exports read the articles of the lists they come from, with sources and main entities."""

import csv
import io
import json
import os
import uuid
from datetime import timedelta

import pytest
from event_fixtures import BASE, feed, story
from sqlalchemy import update
from test_entity_authority_postgres import _article, _entity

from app.articles.models import Article
from app.compare.queries import Spec
from app.compare.routes import compare_export
from app.db.session import session_factory
from app.entities.routes import get_entity_export
from app.events.engine import EVENT_ALGORITHM_VERSION
from app.events.models import Event, EventCluster
from app.events.routes import get_event_export
from app.exports.service import export_rows
from app.feeds.models import FeedArticle

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _csv(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


async def _discover(db, article_id: uuid.UUID, *feeds) -> None:  # type: ignore[no-untyped-def]
    for source in feeds:
        db.add(
            FeedArticle(
                feed_id=source.id,
                article_id=article_id,
                guid=uuid.uuid4().hex,
                feed_title="t",
                feed_url=f"https://example.test/{uuid.uuid4()}",
            )
        )
    await db.flush()


async def test_rows_keep_the_given_order_and_name_the_five_most_relevant_entities() -> None:
    async with session_factory() as db, db.begin():
        names = ["Six", "Five", "Four", "Three", "Two", "One"]
        found = [await _entity(db, name) for name in names]
        noise = await _entity(db, "Monday", hidden=True)
        rich = await _article(
            db,
            [(entity, list(range(6 - index))) for index, entity in enumerate(found)]
            + [(noise, list(range(9)))],
        )
        bare = await _article(db, [])
        wire, daily = await feed(db), await feed(db)
        await _discover(db, rich, wire, daily)
        sources = sorted([wire.name, daily.name])
        article = await db.get(Article, rich)
        assert article is not None
        url, date = article.original_url, article.first_discovered_at

    async with session_factory() as db:
        rows = await export_rows(db, [bare, rich])

    assert [row.article_id for row in rows] == [bare, rich]
    assert rows[0].sources == [] and rows[0].entities == []
    assert (rows[1].title, rows[1].url, rows[1].date) == ("Authority evidence", url, date)
    assert sorted(rows[1].sources) == sources
    assert rows[1].entities == ["Six", "Five", "Four", "Three", "Two"]


async def test_an_event_exports_its_articles_newest_first() -> None:
    async with session_factory() as db, db.begin():
        early = await story(db, [], title="Early report", hours=0)
        late = await story(db, [], title="Late report", hours=3)
        event = Event(algorithm_version=EVENT_ALGORITHM_VERSION, status="active")
        db.add(event)
        await db.flush()
        db.add_all(
            EventCluster(
                event_id=event.id,
                cluster_id=cluster.id,
                algorithm_version=EVENT_ALGORITHM_VERSION,
                score=1,
            )
            for cluster in (early, late)
        )
        await db.flush()
        event_id = event.id

    async with session_factory() as db:
        response = await get_event_export(event_id, db, None, format="csv")  # type: ignore[arg-type]

    assert response.headers["content-disposition"].startswith(
        'attachment; filename="newsintel-event-'
    )
    assert [row["title"] for row in _csv(response.body)] == ["Late report"] * 2 + [
        "Early report"
    ] * 2


async def test_an_entity_exports_the_articles_that_name_it() -> None:
    async with session_factory() as db, db.begin():
        harbour = await _entity(db, "Harbour Authority")
        first = await _article(db, [(harbour, [0])])
        second = await _article(db, [(harbour, [0, 4])])
        await _article(db, [])
        await db.execute(
            update(Article)
            .where(Article.id == first)
            .values(first_discovered_at=BASE - timedelta(days=1))
        )
        entity_id = harbour.id

    async with session_factory() as db:
        response = await get_entity_export(entity_id, db, None, format="json")  # type: ignore[arg-type]

    assert response.media_type == "application/json"
    items = json.loads(response.body)
    assert [item["article_id"] for item in items] == [str(second), str(first)]
    assert all(item["entities"] == ["Harbour Authority"] for item in items)


async def test_a_comparison_exports_one_part() -> None:
    async with session_factory() as db, db.begin():
        wire, daily = await feed(db), await feed(db)
        shared = await story(db, [], title="Both carry it", articles=1, feeds=(wire, daily))
        only = await story(db, [], title="Only the wire", articles=1, feeds=(wire,))
        assert shared and only
        spec = Spec(kind="source", a=str(wire.id), b=str(daily.id), role=None, days=30)

    async with session_factory() as db:
        both = await compare_export(db, None, spec, part="both", format="csv")  # type: ignore[arg-type]
        only_a = await compare_export(db, None, spec, part="a", format="csv")  # type: ignore[arg-type]

    assert [row["title"] for row in _csv(both.body)] == ["Both carry it"]
    assert [row["title"] for row in _csv(only_a.body)] == ["Only the wire"]
    assert both.headers["content-disposition"].startswith(
        'attachment; filename="newsintel-compare-'
    )
