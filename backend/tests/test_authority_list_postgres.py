"""The authority file as a table: article counts, a type filter, two orders, and bulk changes."""

import os
import uuid

import pytest
from sqlalchemy import select
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article, _finish

from app.db.session import session_factory
from app.entities.models import Entity, EntityAuthorityRun

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _table(language: str) -> dict[str, uuid.UUID]:
    """Five roots with 3, 1, 0, 2 and 2 articles; names by key."""
    async with session_factory() as db, db.begin():
        ai = await _named(db, language, "ORG", "ai")
        person = await _named(db, language, "PERSON", "ada vey")
        quiet = await _named(db, language, "ORG", "quiet co")
        topic = await _named(db, language, "ORG", "machine learning", preferred_type="TOPIC")
        brook = await _named(db, language, "PERSON", "brook hale")
        for rows in (
            [(ai, [0]), (person, [5]), (topic, [9])],
            [(ai, [0]), (topic, [9]), (brook, [14])],
            [(ai, [0]), (brook, [14])],
        ):
            await _article(db, rows)
        return {entity.normalized_text: entity.id for entity in (ai, person, quiet, topic, brook)}


async def _pages(params: dict[str, str]) -> list[list[tuple[str, int]]]:
    """Every page of the listing, two rows at a time, as (name, articles)."""
    pages: list[list[tuple[str, int]]] = []
    cursor: str | None = None
    async with _client() as client:
        while True:
            page = (
                await client.get(
                    "/authorities",
                    params={**params, "limit": "2", **({"cursor": cursor} if cursor else {})},
                )
            ).json()
            pages.append([(item["display_name"], item["article_count"]) for item in page["items"]])
            cursor = page["next_cursor"]
            if cursor is None:
                return pages


async def test_the_list_counts_articles_and_pages_by_articles_without_gaps_or_repeats() -> None:
    language = _language()
    await _table(language)

    by_articles = await _pages({"language": language, "sort": "articles"})
    by_name = await _pages({"language": language})

    assert by_articles == [
        [("Ai", 3), ("Brook Hale", 2)],
        [("Machine Learning", 2), ("Ada Vey", 1)],
        [("Quiet Co", 0)],
    ]
    assert [row for page in by_name for row in page] == sorted(
        (row for page in by_articles for row in page), key=lambda row: row[0].lower()
    )


async def test_the_list_filters_by_the_type_shown() -> None:
    language = _language()
    await _table(language)
    async with _client() as client:
        orgs = (
            await client.get("/authorities", params={"language": language, "type": "ORG"})
        ).json()
        mixed = (
            await client.get(
                "/authorities",
                params=[("language", language), ("type", "TOPIC"), ("type", "PERSON")],
            )
        ).json()
        unknown = await client.get("/authorities", params={"type": "WIDGET"})

    assert [item["display_name"] for item in orgs["items"]] == ["Ai", "Quiet Co"]
    assert [item["display_name"] for item in mixed["items"]] == [
        "Ada Vey",
        "Brook Hale",
        "Machine Learning",
    ]
    assert unknown.status_code == 422


async def test_bulk_merge_moves_every_selected_name_under_the_one_kept() -> None:
    language = _language()
    names = await _table(language)
    ids = [names["machine learning"], names["quiet co"]]

    async with _client() as client:
        merged = await client.post(
            "/authorities/bulk",
            json={
                "action": "merge",
                "ids": [str(value) for value in ids],
                "target_id": str(names["ai"]),
            },
            headers=CSRF,
        )
    assert merged.status_code == 200
    runs = [uuid.UUID(run["id"]) for run in merged.json()["runs"]]
    assert len(runs) == 2
    for run_id in runs:
        await _finish(run_id)
    async with session_factory() as db:
        rows = list(await db.scalars(select(Entity).where(Entity.id.in_(ids))))
    assert {row.authority_id for row in rows} == {names["ai"]}


async def test_bulk_type_status_and_hide_change_every_selected_root() -> None:
    language = _language()
    names = await _table(language)
    ids = [str(names["ai"]), str(names["quiet co"])]

    async with _client() as client:
        typed = await client.post(
            "/authorities/bulk",
            json={"action": "set_type", "ids": ids, "entity_type": "TOPIC"},
            headers=CSRF,
        )
        status = await client.post(
            "/authorities/bulk",
            json={"action": "set_status", "ids": ids, "status": "established"},
            headers=CSRF,
        )
        hidden = await client.post(
            "/authorities/bulk", json={"action": "hide", "ids": ids}, headers=CSRF
        )
        shown = await client.post(
            "/authorities/bulk", json={"action": "show", "ids": ids[:1]}, headers=CSRF
        )

    assert [response.status_code for response in (typed, status, hidden, shown)] == [200] * 4
    assert len(typed.json()["runs"]) == 2
    async with session_factory() as db:
        rows = {
            row.normalized_text: (row.kind, row.status, row.hidden)
            for row in await db.scalars(
                select(Entity).where(Entity.id.in_([uuid.UUID(value) for value in ids]))
            )
        }
    assert rows == {
        "ai": ("TOPIC", "established", False),
        "quiet co": ("TOPIC", "established", True),
    }


async def test_a_bulk_change_is_all_or_nothing_and_names_what_it_refused() -> None:
    language = _language()
    names = await _table(language)
    async with session_factory() as db, db.begin():
        variant = await _named(db, language, "ORG", "a.i. corp", authority_id=names["ai"])
        variant_id = variant.id
        runs_before = await db.scalar(
            select(EntityAuthorityRun.id).order_by(EntityAuthorityRun.created_at.desc()).limit(1)
        )

    async with _client() as client:
        refused = await client.post(
            "/authorities/bulk",
            json={
                "action": "set_type",
                "ids": [str(names["quiet co"]), str(variant_id)],
                "entity_type": "PRODUCT",
            },
            headers=CSRF,
        )
        too_many = await client.post(
            "/authorities/bulk",
            json={"action": "hide", "ids": [str(uuid.uuid4()) for _ in range(201)]},
            headers=CSRF,
        )
        no_target = await client.post(
            "/authorities/bulk",
            json={"action": "merge", "ids": [str(names["quiet co"])]},
            headers=CSRF,
        )

    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("A.I. Corp: ")
    assert (too_many.status_code, no_target.status_code) == (422, 422)
    async with session_factory() as db:
        quiet = await db.get(Entity, names["quiet co"])
        assert quiet is not None and quiet.kind == "ORG"
        latest = await db.scalar(
            select(EntityAuthorityRun.id).order_by(EntityAuthorityRun.created_at.desc()).limit(1)
        )
    assert latest == runs_before
