"""The Authority file shows each root's QID and filters roots with or without one."""

import os
import random

import pytest
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import _client

from app.db.session import session_factory
from app.wikidata.models import EntityExternalId

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_roots_carry_their_qid_and_filter_by_it() -> None:
    language = _language()
    qid = f"Q{random.randrange(10**9, 10**10)}"
    async with session_factory() as db, db.begin():
        linked = await _named(db, language, "PERSON", "linked root")
        await _named(db, language, "PERSON", "plain root")
        db.add(EntityExternalId(entity_id=linked.id, scheme="wikidata", value=qid))
        db.add(EntityExternalId(entity_id=linked.id, scheme="viaf", value="1", source="wikidata"))

    async with _client() as api:
        every = (await api.get("/authorities", params={"language": language})).json()
        with_qid = (
            await api.get("/authorities", params={"language": language, "wikidata": "linked"})
        ).json()
        without = (
            await api.get("/authorities", params={"language": language, "wikidata": "unlinked"})
        ).json()
        wrong = await api.get("/authorities", params={"wikidata": "maybe"})

    assert {(row["display_name"], row["qid"]) for row in every["items"]} == {
        ("Linked Root", qid),
        ("Plain Root", None),
    }
    assert [row["display_name"] for row in with_qid["items"]] == ["Linked Root"]
    assert [row["display_name"] for row in without["items"]] == ["Plain Root"]
    assert wrong.status_code == 422
