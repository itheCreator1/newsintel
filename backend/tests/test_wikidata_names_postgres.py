"""The names a linked Wikidata item brings: labels become variants, chosen aliases too.

A name that is already a root with articles is never joined silently: it goes to the "Maybe the
same?" queue with the reason "wikidata name".
"""

import os
import random
import uuid

import pytest
from sqlalchemy import select
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article

from app.db.session import session_factory
from app.nlp.models import Entity, EntityAuthorityRun
from app.nlp.processors import greek_name_key
from app.wikidata.cache import store_items
from app.wikidata.models import EntityExternalId
from app.wikidata.parsing import Item

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

GREEK = "αβγδεζηθικλμνξοπρστυφχψω"


def _qid() -> str:
    return f"Q{random.randrange(10**9, 10**10)}"


def _tag() -> str:
    """A word no other test uses, in Latin letters."""
    return "x" + uuid.uuid4().hex[:8]


def _greek_word() -> str:
    return "".join(random.choice(GREEK) for _ in range(9)).capitalize()


async def _root(name: str, entity_type: str = "PERSON", language: str = "en") -> Entity:
    async with session_factory() as db, db.begin():
        entity = Entity(
            language=language,
            entity_type=entity_type,
            normalized_text=name.casefold(),
            display_text=name,
        )
        db.add(entity)
        await db.flush()
        return entity


async def _cache(item: Item) -> None:
    async with session_factory() as db, db.begin():
        await store_items(db, [item])


async def _names_of(root_id: uuid.UUID) -> dict[tuple[str, str], tuple[str, str]]:
    """(language, normalized) -> (display, name_source) for the root's variants."""
    async with session_factory() as db:
        rows = await db.scalars(select(Entity).where(Entity.authority_id == root_id))
        return {
            (row.language, row.normalized_text): (row.display_text, row.name_source) for row in rows
        }


async def _link(entity_id: uuid.UUID, qid: str, **body: object) -> dict:  # type: ignore[type-arg]
    async with _client() as client:
        response = await client.post(
            f"/entities/{entity_id}/wikidata", json={"qid": qid, **body}, headers=CSRF
        )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_the_labels_become_variants_and_the_articles_are_reindexed() -> None:
    surname = _tag()
    greek = _greek_word()
    root = await _root(f"Alexis {surname}")
    qid = _qid()
    await _cache(
        Item(
            qid=qid,
            state="ok",
            revision=1,
            labels={"en": f"Alexis {surname.capitalize()}", "el": f"Αλέξης {greek}"},
            aliases={"en": [surname.capitalize()]},
        )
    )
    await _link(root.id, qid)

    names = await _names_of(root.id)
    # The English label is the root's own name; the Greek one is new. No alias was chosen.
    assert names == {("el", greek_name_key(f"Αλέξης {greek}")): (f"Αλέξης {greek}", "wikidata")}
    async with session_factory() as db:
        runs = list(
            await db.scalars(
                select(EntityAuthorityRun).where(
                    EntityAuthorityRun.entity_id == root.id, EntityAuthorityRun.kind == "reindex"
                )
            )
        )
    assert len(runs) == 1


async def test_only_the_aliases_the_user_ticks_are_added() -> None:
    surname = _tag()
    root = await _root(f"Kyriakos {surname}")
    qid = _qid()
    await _cache(
        Item(
            qid=qid,
            state="ok",
            revision=1,
            labels={"en": f"Kyriakos {surname}"},
            aliases={"en": [surname, f"K. {surname}"]},
        )
    )
    shown = await _link(root.id, qid, aliases=[{"language": "en", "text": f"K. {surname}"}])
    names = await _names_of(root.id)
    assert names == {("en", f"k. {surname}"): (f"K. {surname}", "wikidata")}
    statuses = {(name["language"], name["text"]): name["status"] for name in shown["names"]}
    assert statuses == {
        ("en", f"Kyriakos {surname}"): "this_entity",
        ("en", f"K. {surname}"): "this_entity",
        ("en", surname): "absent",
    }
    kinds = {name["text"]: name["kind"] for name in shown["names"]}
    assert kinds[f"Kyriakos {surname}"] == "label"
    assert kinds[surname] == "alias"


async def test_an_alias_can_be_added_later_but_only_one_the_item_has() -> None:
    surname = _tag()
    root = await _root(f"Nikos {surname}")
    qid = _qid()
    await _cache(
        Item(
            qid=qid,
            state="ok",
            revision=1,
            labels={"en": f"Nikos {surname}"},
            aliases={"en": [f"N. {surname}"]},
        )
    )
    await _link(root.id, qid)
    async with _client() as client:
        added = await client.post(
            f"/entities/{root.id}/wikidata/names",
            json={"names": [{"language": "en", "text": f"N. {surname}"}]},
            headers=CSRF,
        )
        assert added.status_code == 200, added.text
        refused = await client.post(
            f"/entities/{root.id}/wikidata/names",
            json={"names": [{"language": "en", "text": "Someone Else"}]},
            headers=CSRF,
        )
        assert refused.status_code == 422
    assert ("en", f"n. {surname}") in await _names_of(root.id)


async def test_a_name_already_a_root_with_articles_is_suggested_not_joined() -> None:
    surname = _tag()
    root = await _root(f"Alexis {surname}")
    greek = _greek_word()
    other = await _root(f"Αλέξης {greek}", language="el")
    async with session_factory() as db, db.begin():
        await _article(db, [(await db.get(Entity, other.id), [0])])  # type: ignore[list-item]
        other_key = greek_name_key(f"Αλέξης {greek}")
        stored = await db.get(Entity, other.id)
        assert stored is not None
        stored.normalized_text = other_key
    # A name nobody uses yet is simply taken in.
    unused = await _root(f"Tsipras {surname}")
    qid = _qid()
    await _cache(
        Item(
            qid=qid,
            state="ok",
            revision=1,
            labels={"en": f"Alexis {surname}", "el": f"Αλέξης {greek}"},
            aliases={"en": [f"Tsipras {surname}"]},
        )
    )
    shown = await _link(root.id, qid, aliases=[{"language": "en", "text": f"Tsipras {surname}"}])

    async with session_factory() as db:
        kept = await db.get(Entity, other.id)
        taken = await db.get(Entity, unused.id)
    assert kept is not None and kept.authority_id is None
    assert taken is not None and taken.authority_id == root.id
    statuses = {name["text"]: name["status"] for name in shown["names"]}
    assert statuses[f"Αλέξης {greek}"] == "other_entity"

    async with _client() as client:
        found = (await client.get("/authorities/suggestions", params={"limit": 200})).json()
    pairs = {
        frozenset((item["root"]["id"], item["variant"]["id"])): item for item in found["items"]
    }
    suggestion = pairs[frozenset((str(root.id), str(other.id)))]
    assert suggestion["reasons"][0] == "wikidata name"
    assert suggestion["score"] >= 0.9


async def test_roots_linked_to_different_items_are_never_suggested_as_one() -> None:
    word = _tag()
    first = await _root(f"Georgia {word}", entity_type="GPE")
    second = await _root(f"Georgia {word}s", entity_type="GPE")
    async with session_factory() as db, db.begin():
        db.add(EntityExternalId(entity_id=first.id, scheme="wikidata", value=_qid()))
        db.add(EntityExternalId(entity_id=second.id, scheme="wikidata", value=_qid()))
    async with _client() as client:
        found = (await client.get("/authorities/suggestions", params={"limit": 200})).json()
    pairs = {frozenset((item["root"]["id"], item["variant"]["id"])) for item in found["items"]}
    assert frozenset((str(first.id), str(second.id))) not in pairs
