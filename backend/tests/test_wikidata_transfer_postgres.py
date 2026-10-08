"""Wikidata links travel in the authority file (version 2); the cache does not, it refills.

A rebuilt database gets the user's decisions back from the file, and a refresh run per link
fetches the items again. A version 1 file still imports.
"""

import json
import os
import random
import uuid

import pytest
from sqlalchemy import select
from test_authority_suggestions_postgres import _language, _named

from app.db.session import session_factory
from app.entities.transfer import export_authorities, import_authorities
from app.nlp.models import Entity, EntityAuthorityChange
from app.wikidata.models import EntityExternalId, WikidataRun

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _qid() -> str:
    return f"Q{random.randrange(10**9, 10**10)}"


async def _linked_file(language: str, qid: str) -> None:
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "PERSON", "nora vale")
        await _named(
            db, language, "PERSON", "νόρα βέιλ", authority_id=root.id, name_source="wikidata"
        )
        db.add(EntityExternalId(entity_id=root.id, scheme="wikidata", value=qid))
        db.add(EntityExternalId(entity_id=root.id, scheme="viaf", value="777", source="wikidata"))


def _moved(data: dict, old: tuple[str, str], new: tuple[str, str]) -> dict:  # type: ignore[type-arg]
    """The file as another database would read it: other language, other QID."""
    text = json.dumps(data)
    for before, after in zip(old, new, strict=True):
        text = text.replace(f'"{before}"', f'"{after}"')
    return json.loads(text)  # type: ignore[no-any-return]


async def _links(language: str, name: str) -> tuple[uuid.UUID, dict[str, tuple[str, str]]]:
    async with session_factory() as db:
        root = await db.scalar(
            select(Entity).where(Entity.language == language, Entity.normalized_text == name)
        )
        assert root is not None
        rows = await db.scalars(
            select(EntityExternalId).where(EntityExternalId.entity_id == root.id)
        )
        return root.id, {row.scheme: (row.value, row.source) for row in rows}


async def test_links_and_name_sources_travel_and_queue_a_fetch() -> None:
    source, target = _language(), _language()
    old, new = _qid(), _qid()
    await _linked_file(source, old)
    async with session_factory() as db:
        exported = await export_authorities(db, language=source)

    assert exported["version"] == 2
    (nora,) = exported["entities"]
    assert nora["external_ids"] == [
        {"scheme": "viaf", "value": "777", "source": "wikidata"},
        {"scheme": "wikidata", "value": old, "source": "user"},
    ]
    assert nora["name_source"] == "ner"
    assert [item["name_source"] for item in nora["variants"]] == ["wikidata"]

    data = _moved(exported, (source, old), (target, new))
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)
    assert report.conflicts == [] and report.linked == 1

    root_id, links = await _links(target, "nora vale")
    assert links == {"wikidata": (new, "user"), "viaf": ("777", "wikidata")}
    async with session_factory() as db:
        variant = await db.scalar(
            select(Entity).where(Entity.language == target, Entity.authority_id == root_id)
        )
        assert variant is not None and variant.name_source == "wikidata"
        actions = list(
            await db.scalars(
                select(EntityAuthorityChange.action).where(
                    EntityAuthorityChange.entity_id == root_id
                )
            )
        )
        assert "wikidata_linked" in actions
        runs = list(await db.scalars(select(WikidataRun).where(WikidataRun.entity_id == root_id)))
        # The cache does not travel: one refresh run fetches the item again.
        assert [(run.kind, run.status, run.add_labels) for run in runs] == [
            ("refresh", "queued", False)
        ]
        again = await export_authorities(db, language=target)
    assert _moved(again, (target, new), (source, old))["entities"] == exported["entities"]

    async with session_factory() as db, db.begin():
        second = await import_authorities(db, data)
    assert second.conflicts == [] and second.linked == 0


async def test_a_qid_another_root_holds_or_a_second_qid_is_a_conflict() -> None:
    source, target = _language(), _language()
    held, other = _qid(), _qid()
    await _linked_file(source, held)
    async with session_factory() as db:
        exported = await export_authorities(db, language=source)

    # The QID is held by the source root: importing it under another root is a conflict.
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, _moved(exported, (source,), (target,)))
    assert report.linked == 0
    assert any(held in conflict for conflict in report.conflicts)
    _, links = await _links(target, "nora vale")
    assert "wikidata" not in links

    async with session_factory() as db, db.begin():
        report = await import_authorities(db, _moved(exported, (held,), (other,)))
    assert report.linked == 0
    assert any(f"already linked to {held}" in conflict for conflict in report.conflicts)
    _, links = await _links(source, "nora vale")
    assert links["wikidata"] == (held, "user")


async def test_a_version_1_file_still_imports() -> None:
    language = _language()
    data = {
        "format": "newsintel-authority-file",
        "version": 1,
        "entities": [
            {
                "language": language,
                "entity_type": "PERSON",
                "normalized_text": "ida rune",
                "display_text": "Ida Rune",
                "variants": [
                    {
                        "language": language,
                        "entity_type": "PERSON",
                        "normalized_text": "i. rune",
                        "display_text": "I. Rune",
                    }
                ],
            }
        ],
    }
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)
    assert report.conflicts == [] and report.created == 2
    async with session_factory() as db:
        variant = await db.scalar(
            select(Entity).where(Entity.language == language, Entity.normalized_text == "i. rune")
        )
        assert variant is not None and variant.name_source == "ner"
