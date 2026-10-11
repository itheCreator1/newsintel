"""The Wikidata link says which type the item's classes point to, from the cache alone."""

import dataclasses
import os

import pytest
from sqlalchemy.dialects.postgresql import insert
from test_authority_suggestions_postgres import _language
from test_entity_authority_api_postgres import CSRF, _client
from test_wikidata_links_postgres import _cache, _full, _qid, _roots

from app.db.session import session_factory
from app.wikidata.models import WikidataClass

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _classes(*rows: tuple[str, list[str]]) -> None:
    async with session_factory() as db, db.begin():
        for qid, parents in rows:
            statement = insert(WikidataClass).values(qid=qid, subclass_of=parents)
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=["qid"], set_={"subclass_of": statement.excluded.subclass_of}
                )
            )


async def _linked_type(entity_type: str, name: str, instance_of: list[str]) -> str | None:
    language = _language()
    ids = await _roots(language, name, entity_type=entity_type)
    qid = _qid()
    await _cache(dataclasses.replace(_full(qid, name.title()), instance_of=instance_of))
    async with _client() as client:
        linked = await client.post(
            f"/entities/{ids[name]}/wikidata", json={"qid": qid}, headers=CSRF
        )
        assert linked.status_code == 200, linked.text
        shown = (await client.get(f"/entities/{ids[name]}/wikidata")).json()
    assert linked.json()["wikidata_type"] == shown["wikidata_type"]
    return shown["wikidata_type"]


async def test_a_human_item_says_person_whatever_the_root_is_called() -> None:
    assert await _linked_type("ORG", "ada vey", ["Q5"]) == "PERSON"
    assert await _linked_type("PERSON", "ada wren", ["Q5"]) == "PERSON"


async def test_a_class_under_organization_in_the_cache_says_org() -> None:
    party, movement = _qid(), _qid()
    await _classes((party, [movement]), (movement, ["Q43229"]))
    assert await _linked_type("PERSON", "the reds", [party]) == "ORG"


async def test_the_roots_own_type_wins_when_the_classes_fit_several() -> None:
    city = _qid()
    # A city is a first-level place and a geographic location at once.
    await _classes((city, ["Q515", "Q2221906"]))
    assert await _linked_type("LOCATION", "port vale", [city]) == "LOCATION"
    assert await _linked_type("ORG", "port lune", [city]) == "GPE"


async def test_an_unknown_class_says_nothing_and_nothing_is_fetched() -> None:
    # Not in the cache: reading never asks Wikidata, so there is no hint.
    assert await _linked_type("ORG", "odd thing", [_qid()]) is None
