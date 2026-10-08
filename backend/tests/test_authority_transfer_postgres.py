import json
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_postgres import _article

from app.db.session import session_factory
from app.nlp.models import Entity, EntityAuthorityChange, EntityAuthorityRun, EntityDistinct

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _file(language: str) -> None:
    """A small authority file: a root with two variants, an ambiguous name, a distinct pair."""
    async with session_factory() as db, db.begin():
        root = await _named(
            db,
            language,
            "PERSON",
            "hugo brandt",
            preferred_text="Brandt, Hugo",
            status="established",
            note="Economist, not the footballer",
        )
        await _named(db, language, "PERSON", "h. brandt", authority_id=root.id)
        await _named(db, language, "PERSON", "brandt", authority_id=root.id, ambiguous=True)
        await _named(db, language, "PERSON", "lee", ambiguous=True)
        olga = await _named(db, language, "PERSON", "olga tam")
        initial = await _named(db, language, "PERSON", "o. tam")
        low, high = sorted((olga.id, initial.id))
        db.add(EntityDistinct(a_id=low, b_id=high))
        # Nothing to record: a plain name stays out of the file.
        await _named(db, language, "PERSON", "plain name")


def _relabel(data: dict, old: str, new: str) -> dict:  # type: ignore[type-arg]
    data = json.loads(json.dumps(data).replace(f'"{old}"', f'"{new}"'))
    data.pop("exported_at")
    return data


async def _entity(db, language: str, name: str) -> Entity:  # type: ignore[no-untyped-def]
    entity = await db.scalar(
        select(Entity).where(Entity.language == language, Entity.normalized_text == name)
    )
    assert entity is not None, name
    return entity


async def test_export_then_import_round_trips() -> None:
    from app.entities.transfer import export_authorities, import_authorities

    source, target = _language(), _language()
    await _file(source)
    async with session_factory() as db:
        exported = await export_authorities(db, language=source)

    assert exported["format"] == "newsintel-authority-file"
    assert exported["version"] == 2
    assert [item["normalized_text"] for item in exported["entities"]] == [
        "hugo brandt",
        "lee",
    ]
    hugo = exported["entities"][0]
    assert hugo["preferred_text"] == "Brandt, Hugo"
    assert [item["normalized_text"] for item in hugo["variants"]] == ["brandt", "h. brandt"]
    assert hugo["variants"][0]["ambiguous"] is True
    assert [[side["normalized_text"] for side in pair] for pair in exported["distinct"]] == [
        ["o. tam", "olga tam"]
    ]

    # Into a fresh language, as into a rebuilt database: every name is new there.
    async with session_factory() as db, db.begin():
        report = await import_authorities(
            db, json.loads(json.dumps(_relabel(exported, source, target)))
        )
    assert (report.created, report.merged, report.distinct_added) == (6, 2, 1)
    assert report.conflicts == []

    async with session_factory() as db:
        root = await _entity(db, target, "hugo brandt")
        assert (root.name, root.status, root.note) == (
            "Brandt, Hugo",
            "established",
            "Economist, not the footballer",
        )
        assert (await _entity(db, target, "h. brandt")).authority_id == root.id
        assert (await _entity(db, target, "brandt")).ambiguous is True
        assert (await _entity(db, target, "lee")).ambiguous is True
        assert (
            await db.scalar(
                select(func.count())
                .select_from(EntityAuthorityChange)
                .where(
                    EntityAuthorityChange.entity_id.in_([root.id]),
                    EntityAuthorityChange.action == "renamed",
                )
            )
            == 1
        )
        again = await export_authorities(db, language=target)

    assert _relabel(again, target, source) == _relabel(exported, source, source)


async def test_import_is_idempotent() -> None:
    from app.entities.transfer import export_authorities, import_authorities

    source, target = _language(), _language()
    await _file(source)
    async with session_factory() as db:
        data = _relabel(await export_authorities(db, language=source), source, target)
    async with session_factory() as db, db.begin():
        await import_authorities(db, data)
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)

    assert (report.created, report.merged, report.updated, report.distinct_added) == (0, 0, 0, 0)
    assert report.unchanged == 5
    assert report.conflicts == []


async def test_an_existing_name_with_articles_is_merged_by_a_run() -> None:
    from app.entities.transfer import import_authorities

    language = _language()
    async with session_factory() as db, db.begin():
        pia = await _named(db, language, "PERSON", "pia sol")
        initial = await _named(db, language, "PERSON", "p. sol")
        await _article(db, [(pia, [0])])
        await _article(db, [(initial, [0])])
        initial_id = initial.id

    data = {
        "format": "newsintel-authority-file",
        "version": 1,
        "entities": [
            {
                "language": language,
                "entity_type": "PERSON",
                "normalized_text": "pia sol",
                "display_text": "Pia Sol",
                "preferred_text": None,
                "status": "provisional",
                "ambiguous": False,
                "note": None,
                "variants": [
                    {
                        "language": language,
                        "entity_type": "PERSON",
                        "normalized_text": "p. sol",
                        "display_text": "P. Sol",
                        "ambiguous": False,
                        "note": None,
                    }
                ],
            }
        ],
        "distinct": [],
    }
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)

    assert (report.created, report.merged) == (0, 1)
    async with session_factory() as db:
        run = await db.scalar(
            select(EntityAuthorityRun).where(EntityAuthorityRun.entity_id == initial_id)
        )
        assert run is not None and run.kind == "merge"


async def test_a_conflict_is_reported_and_left_alone() -> None:
    from app.entities.transfer import import_authorities

    language = _language()
    async with session_factory() as db, db.begin():
        xavier = await _named(db, language, "PERSON", "xavier lane")
        await _named(db, language, "PERSON", "x. lane", authority_id=xavier.id)
        xavier_id = xavier.id

    def name(text: str) -> dict[str, object]:
        return {
            "language": language,
            "entity_type": "PERSON",
            "normalized_text": text,
            "display_text": text.title(),
            "ambiguous": False,
            "note": None,
        }

    data = {
        "format": "newsintel-authority-file",
        "version": 1,
        "entities": [
            {
                **name("xena lane"),
                "preferred_text": None,
                "status": "established",
                "variants": [name("x. lane"), name("x lane")],
            }
        ],
        "distinct": [],
    }
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)

    assert len(report.conflicts) == 1 and "x. lane" in report.conflicts[0]
    async with session_factory() as db:
        assert (await _entity(db, language, "x. lane")).authority_id == xavier_id
        xena = await _entity(db, language, "xena lane")
        assert xena.status == "established"
        assert (await _entity(db, language, "x lane")).authority_id == xena.id


async def test_the_cli_exports_a_file_and_imports_it_as_a_dry_run_unless_applied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import run_authority_export, run_authority_import

    source, target = _language(), _language()
    await _file(source)
    exported = tmp_path / "authorities.json"
    await run_authority_export(exported, language=source)
    relabelled = tmp_path / "relabelled.json"
    relabelled.write_text(exported.read_text().replace(f'"{source}"', f'"{target}"'))

    await run_authority_import(relabelled, apply=False)
    assert "dry-run" in capsys.readouterr().out
    async with session_factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Entity).where(Entity.language == target)
            )
            == 0
        )

    await run_authority_import(relabelled, apply=True)
    assert "created=6" in capsys.readouterr().out
    async with session_factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Entity).where(Entity.language == target)
            )
            == 6
        )

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "something else"}))
    with pytest.raises(SystemExit, match="authority file"):
        await run_authority_import(broken, apply=True)


async def test_ids_never_travel_in_the_file() -> None:
    from app.entities.transfer import export_authorities

    language = _language()
    await _file(language)
    async with session_factory() as db:
        text = json.dumps(await export_authorities(db, language=language))
    # Ids differ from one database to the next; the file names entities by their identity.
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
