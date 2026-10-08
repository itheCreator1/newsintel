import os
import uuid

import pytest
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import CSRF, _client

from app.db.session import session_factory

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_the_authority_file_lists_roots_with_their_variants_and_filters_by_status() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        kept = await _named(db, language, "PERSON", "ines alvar", status="established")
        root = await _named(db, language, "PERSON", "hugo brandt", preferred_text="Brandt, Hugo")
        await _named(db, language, "PERSON", "h. brandt", authority_id=root.id)
        await _named(db, language, "PERSON", "brandt", authority_id=root.id)
        ids = kept.id, root.id

    async with _client() as client:
        every = (await client.get("/authorities", params={"language": language})).json()
        provisional = (
            await client.get("/authorities", params={"language": language, "status": "provisional"})
        ).json()
        by_name = (await client.get("/authorities", params={"language": language, "q": "hug"})).json()
        paged = (await client.get("/authorities", params={"language": language, "limit": 1})).json()
        rest = (
            await client.get(
                "/authorities",
                params={"language": language, "limit": 1, "cursor": paged["next_cursor"]},
            )
        ).json()

    # Roots only, by name; a variant is listed under its root.
    assert [(item["id"], item["display_name"], item["variant_count"]) for item in every["items"]] == [
        (str(ids[1]), "Brandt, Hugo", 2),
        (str(ids[0]), "Ines Alvar", 0),
    ]
    assert every["items"][1]["status"] == "established"
    assert [item["id"] for item in provisional["items"]] == [str(ids[1])]
    assert [item["id"] for item in by_name["items"]] == [str(ids[1])]
    assert [item["id"] for item in paged["items"] + rest["items"]] == [str(ids[1]), str(ids[0])]
    assert rest["next_cursor"] is None


async def test_suggestions_are_offered_until_approved_or_rejected() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "PERSON", "jon tarr")
        await _named(db, language, "PERSON", "j. tarr")
        await _named(db, language, "ORG", "north atlantic council")
        await _named(db, language, "ORG", "nac")

    async with _client() as client:
        offered = (await client.get("/authorities/suggestions", params={"language": language})).json()
        by_root = {item["root"]["display_name"]: item for item in offered["items"]}
        assert set(by_root) == {"Jon Tarr", "North Atlantic Council"}
        person, org = by_root["Jon Tarr"], by_root["North Atlantic Council"]
        assert person["variant"]["display_name"] == "J. Tarr"
        assert "initials" in person["reasons"] and person["score"] > 0
        assert org["root"]["entity_type"] == "ORG"

        rejected = await client.post(
            f"/entities/{person['root']['id']}/distinct/{person['variant']['id']}", headers=CSRF
        )
        approved = await client.post(
            f"/entities/{org['variant']['id']}/merge",
            json={"target_id": org["root"]["id"]},
            headers=CSRF,
        )
        after = (await client.get("/authorities/suggestions", params={"language": language})).json()

    assert (rejected.status_code, approved.status_code) == (204, 202)
    assert after["items"] == []


async def test_the_authority_history_is_newest_first() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        entity = await _named(db, language, "PERSON", "kai lund")
        entity_id = entity.id

    async with _client() as client:
        await client.patch(f"/entities/{entity_id}", json={"status": "established"}, headers=CSRF)
        await client.patch(f"/entities/{entity_id}", json={"preferred_text": "Lund, Kai"}, headers=CSRF)
        latest = (await client.get("/authorities/history", params={"limit": 2})).json()
        older = (
            await client.get(
                "/authorities/history", params={"limit": 2, "cursor": latest["next_cursor"]}
            )
        ).json()
        bad = await client.get("/authorities/history", params={"cursor": "not-a-cursor"})

    assert [(item["action"], item["entity_id"]) for item in latest["items"]] == [
        ("renamed", str(entity_id)),
        ("status_changed", str(entity_id)),
    ]
    assert latest["items"][0]["entity_name"] == "Lund, Kai"
    assert str(entity_id) not in {item["entity_id"] for item in older["items"]}
    assert bad.status_code == 400
    assert uuid.UUID(latest["items"][0]["id"])
