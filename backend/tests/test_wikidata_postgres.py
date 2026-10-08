"""The Wikidata tables, the shared throttle in Postgres, and the item cache."""

import asyncio
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_authority_suggestions_postgres import _language, _named

from app.core.config import Settings
from app.db.session import session_factory
from app.nlp.models import EntityAuthorityChange
from app.wikidata.cache import cached_items, store_items
from app.wikidata.client import WikidataClient
from app.wikidata.errors import WikidataBudgetSpent, WikidataPaused, WikidataUnavailable
from app.wikidata.models import EntityExternalId, WikidataItem, WikidataRequestCount
from app.wikidata.parsing import parse_entities
from app.wikidata.throttle import PostgresThrottle

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

FIXTURES = Path(__file__).parent / "fixtures" / "wikidata"


def _settings(**values: Any) -> Settings:
    return Settings(wikidata_contact="newsintel-owner@example.org", **values)


async def _reset_throttle() -> None:
    async with session_factory() as db, db.begin():
        await db.execute(text("DELETE FROM wikidata_throttle"))
        await db.execute(text("DELETE FROM wikidata_request_counts"))


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _empty_search(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"search": [], "success": 1})


async def test_the_migration_adds_the_wikidata_tables_and_name_sources() -> None:
    async with session_factory() as db:
        for table in (
            "entity_external_ids",
            "wikidata_items",
            "wikidata_classes",
            "wikidata_searches",
            "wikidata_candidates",
            "wikidata_runs",
            "wikidata_throttle",
            "wikidata_request_counts",
        ):
            assert await db.scalar(text(f"SELECT to_regclass('{table}') IS NOT NULL")), table
    language = _language()
    async with session_factory() as db, db.begin():
        entity = await _named(db, language, "PERSON", "ana sources")
        await db.refresh(entity)
        assert entity.name_source == "ner"
        entity.name_source = "wikidata"
        await db.flush()
    with pytest.raises(IntegrityError):
        async with session_factory() as db, db.begin():
            await _named(db, language, "PERSON", "bad source", name_source="guess")


async def test_the_history_takes_the_wikidata_actions() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        entity = await _named(db, language, "PERSON", "ana history")
        for action in (
            "wikidata_linked",
            "wikidata_unlinked",
            "wikidata_redirected",
            "wikidata_missing",
        ):
            db.add(EntityAuthorityChange(action=action, entity_id=entity.id))
        await db.flush()


async def test_one_qid_names_one_root_and_values_keep_their_form() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        first = await _named(db, language, "PERSON", "first holder")
        second = await _named(db, language, "PERSON", "second holder")
        db.add(EntityExternalId(entity_id=first.id, scheme="wikidata", value="Q8100001"))
        db.add(
            EntityExternalId(entity_id=first.id, scheme="viaf", value="8100001", source="wikidata")
        )
        # The same VIAF number on two items is Wikidata's problem, not a reason to refuse a link.
        db.add(
            EntityExternalId(entity_id=second.id, scheme="viaf", value="8100001", source="wikidata")
        )
        await db.flush()
        ids = (first.id, second.id)

    for entity_id, scheme, value in (
        (ids[1], "wikidata", "Q8100001"),  # a QID already held by another root
        (ids[1], "wikidata", "8100001"),  # not a QID
        (ids[1], "orcid", "0000"),  # no such scheme
        (ids[0], "wikidata", "Q8100002"),  # a second QID for one root
    ):
        with pytest.raises(IntegrityError):
            async with session_factory() as db, db.begin():
                db.add(EntityExternalId(entity_id=entity_id, scheme=scheme, value=value))
                await db.flush()


async def test_two_workers_never_send_at_once_and_keep_the_pace() -> None:
    await _reset_throttle()
    settings = _settings(wikidata_min_interval_seconds=1)
    spans: list[tuple[float, float]] = []
    loop = asyncio.get_running_loop()

    async def handler(request: httpx.Request) -> httpx.Response:
        began = loop.time()
        await asyncio.sleep(0.2)
        spans.append((began, loop.time()))
        return _empty_search(request)

    async def worker(name: str) -> None:
        async with WikidataClient(
            settings, PostgresThrottle(settings), transport=httpx.MockTransport(handler)
        ) as client:
            for n in range(2):
                await client.search(f"{name} {n}", "en")

    await asyncio.gather(worker("a"), worker("b"))
    spans.sort()
    assert len(spans) == 4
    for (_, ended), (began, _) in zip(spans, spans[1:], strict=False):
        # Never two at once, and the pace holds across workers.
        assert began - ended >= 0.95


async def test_a_pause_survives_a_restart() -> None:
    await _reset_throttle()
    clock = Clock(datetime.now(UTC))
    settings = _settings()

    def limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "120"})

    async with WikidataClient(
        settings,
        PostgresThrottle(settings, clock=clock, sleep=clock.sleep),
        transport=httpx.MockTransport(limited),
    ) as client:
        with pytest.raises(WikidataUnavailable):
            await client.search("a", "en")

    # A new process: a new throttle, the same row.
    sent: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _empty_search(request)

    clock.now += timedelta(minutes=10)
    async with WikidataClient(
        settings,
        PostgresThrottle(settings, clock=clock, sleep=clock.sleep),
        transport=httpx.MockTransport(recording),
    ) as client:
        with pytest.raises(WikidataPaused) as paused:
            await client.search("b", "en")
        assert paused.value.reason == "rate_limited"
        clock.now += timedelta(minutes=6)
        await client.search("c", "en")
    assert len(sent) == 1
    await _reset_throttle()


async def test_the_daily_budget_is_shared_by_every_worker() -> None:
    await _reset_throttle()
    clock = Clock(datetime(2026, 10, 8, 9, 0, tzinfo=UTC))
    settings = _settings(wikidata_daily_request_budget=3)

    def client() -> WikidataClient:
        return WikidataClient(
            settings,
            PostgresThrottle(settings, clock=clock, sleep=clock.sleep),
            transport=httpx.MockTransport(_empty_search),
        )

    async with client() as first, client() as second:
        await first.search("a", "en")
        await second.search("b", "en")
        await first.search("c", "en")
        with pytest.raises(WikidataBudgetSpent):
            await second.search("d", "en")
        clock.now = datetime(2026, 10, 9, 0, 0, 5, tzinfo=UTC)
        await second.search("e", "en")

    async with session_factory() as db:
        counts = {
            (row.day, row.kind, row.outcome): row.count
            for row in await db.scalars(select(WikidataRequestCount))
        }
    assert counts == {
        (date(2026, 10, 8), "search", "ok"): 3,
        (date(2026, 10, 9), "search", "ok"): 1,
    }
    await _reset_throttle()


async def test_items_are_cached_and_a_newer_revision_replaces_them() -> None:
    body = json.loads((FIXTURES / "entities.json").read_text())
    items = parse_entities(body, languages=("el", "en"))
    async with session_factory() as db, db.begin():
        await db.execute(
            text("DELETE FROM wikidata_items WHERE qid LIKE 'Q900000%' OR qid = 'Q42'")
        )
        await store_items(db, items)

    async with session_factory() as db:
        cached = await cached_items(db, ["Q42", "Q9000003", "Q9000005", "Q404"])
    assert set(cached) == {"Q42", "Q9000003", "Q9000005"}
    adams = cached["Q42"]
    assert adams.labels == {"en": "Douglas Adams", "el": "Ντάγκλας Άνταμς"}
    assert adams.ids == {"viaf": "113230702", "isni": "0000 0000 8045 6315", "lcnaf": "n80076765"}
    assert adams.instance_of == ["Q5"]
    assert adams.revision == 2215441093
    assert adams.state == "ok"
    assert adams.claims_fetched is True
    assert cached["Q9000003"].state == "redirected"
    assert cached["Q9000003"].redirect_to == "Q9000004"
    assert cached["Q9000005"].state == "missing"
    first_fetch = adams.fetched_at

    body["entities"]["Q42"]["lastrevid"] += 1
    body["entities"]["Q42"]["labels"]["en"]["value"] = "Douglas Noël Adams"
    async with session_factory() as db, db.begin():
        await store_items(db, parse_entities(body, languages=("el", "en")))
    async with session_factory() as db:
        again = (await cached_items(db, ["Q42"]))["Q42"]
    assert again.labels["en"] == "Douglas Noël Adams"
    assert again.revision == 2215441094
    assert again.fetched_at >= first_fetch


async def test_a_light_fetch_does_not_erase_claims_already_cached() -> None:
    body = json.loads((FIXTURES / "entities.json").read_text())
    async with session_factory() as db, db.begin():
        await db.execute(text("DELETE FROM wikidata_items WHERE qid = 'Q42'"))
        await store_items(db, parse_entities(body, languages=("el", "en")))
    light = json.loads((FIXTURES / "entities.json").read_text())
    del light["entities"]["Q42"]["claims"]
    async with session_factory() as db, db.begin():
        await store_items(db, parse_entities(light, languages=("el", "en")))
    async with session_factory() as db:
        adams = (await cached_items(db, ["Q42"]))["Q42"]
    assert adams.instance_of == ["Q5"]
    assert adams.claims_fetched is True


async def test_country_spellings_are_marked_as_seeded() -> None:
    from app.nlp.authority import seed_countries
    from app.nlp.models import Entity

    async with session_factory() as db, db.begin():
        await seed_countries(db)
    async with session_factory() as db:
        sources = dict(
            (
                await db.execute(
                    select(Entity.normalized_text, Entity.name_source).where(
                        Entity.language == "en",
                        Entity.entity_type == "GPE",
                        Entity.normalized_text.in_(("usa", "united states")),
                    )
                )
            ).all()
        )
    # The spelling the seed wrote; the root keeps whatever made it.
    assert sources["usa"] == "seed"


async def test_downgrade_keeps_links_unless_told_to_discard_them() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        entity = await _named(db, language, "PERSON", "kept link")
        db.add(
            EntityExternalId(
                entity_id=entity.id, scheme="wikidata", value=f"Q{uuid.uuid4().int % 10**9 + 10**9}"
            )
        )
        await db.flush()
        entity_id = entity.id
        entities_before = await db.scalar(text("SELECT count(*) FROM nlp_entities"))

    config = Config("alembic.ini")
    with pytest.raises(RuntimeError, match="authority export"):
        await asyncio.to_thread(command.downgrade, config, "0020")
    async with session_factory() as db:
        assert await db.scalar(
            select(EntityExternalId.value).where(EntityExternalId.entity_id == entity_id)
        )

    os.environ["NEWSINTEL_MIGRATION_DISCARD_WIKIDATA"] = "1"
    try:
        await asyncio.to_thread(command.downgrade, config, "0020")
    finally:
        del os.environ["NEWSINTEL_MIGRATION_DISCARD_WIKIDATA"]
    try:
        async with session_factory() as db:
            assert await db.scalar(text("SELECT to_regclass('entity_external_ids') IS NULL"))
            assert await db.scalar(text("SELECT to_regclass('wikidata_throttle') IS NULL"))
            assert await db.scalar(text("SELECT count(*) FROM nlp_entities")) == entities_before
            columns = await db.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'nlp_entities'"
                )
            )
            assert "name_source" not in set(columns)
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with session_factory() as db:
        assert await db.scalar(text("SELECT to_regclass('entity_external_ids') IS NOT NULL"))
        # The cache went with the downgrade; it refills from the links' QIDs.
        assert await db.scalar(select(WikidataItem.qid).limit(1)) is None
