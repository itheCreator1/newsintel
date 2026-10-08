import asyncio
import json
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article

from app.db.session import session_factory
from app.feeds.models import Article
from app.nlp.models import Entity

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _entities(
    language: str, *names: tuple[str, str], **variants: str
) -> dict[str, uuid.UUID]:
    """Roots by (type, name); `variants` ties a name (key) to a root's name (value)."""
    ids: dict[str, uuid.UUID] = {}
    async with session_factory() as db, db.begin():
        for entity_type, name in names:
            ids[name] = (await _named(db, language, entity_type, name)).id
        for variant, root in variants.items():
            ids[variant] = (await _named(db, language, "ORG", variant, authority_id=ids[root])).id
    return ids


async def _link(client, entity_id: uuid.UUID, label: str, target_id: uuid.UUID, **values: object):  # type: ignore[no-untyped-def]
    return await client.post(
        f"/entities/{entity_id}/see-also",
        json={"label": label, "target_id": str(target_id), **values},
        headers=CSRF,
    )


def _seen(page: dict) -> list[tuple[str, str]]:  # type: ignore[type-arg]
    return sorted((item["label"], item["entity"]["display_name"]) for item in page["items"])


async def test_a_link_reads_from_both_sides_and_lands_on_roots() -> None:
    language = _language()
    ids = await _entities(language, ("ORG", "facebook"), ("ORG", "meta platforms"), fb="facebook")

    async with _client() as client:
        # Added from a variant's id: stored between the roots.
        added = await _link(
            client, ids["fb"], "later_name", ids["meta platforms"], valid_from="2021-10"
        )
        old = (await client.get(f"/entities/{ids['facebook']}/see-also")).json()
        new = (await client.get(f"/entities/{ids['meta platforms']}/see-also")).json()

    assert added.status_code == 201
    assert added.json()["label"] == "later_name"
    assert added.json()["entity"]["id"] == str(ids["meta platforms"])
    assert _seen(old) == [("later_name", "Meta Platforms")]
    assert _seen(new) == [("earlier_name", "Facebook")]
    assert new["items"][0]["valid_from"] == "2021-10"
    assert new["items"][0]["entity"]["entity_type"] == "ORG"
    assert "later_name" in old["labels"] and "leader_of" not in old["labels"]


async def test_types_dates_notes_and_duplicates_are_checked() -> None:
    language = _language()
    ids = await _entities(language, ("PERSON", "ada vey"), ("PERSON", "bo lin"), ("ORG", "acme"))

    async with _client() as client:
        part = await _link(client, ids["ada vey"], "part_of", ids["bo lin"])
        date = await _link(client, ids["ada vey"], "member_of", ids["acme"], valid_from="2021-13")
        order = await _link(
            client, ids["ada vey"], "member_of", ids["acme"], valid_from="2022", valid_to="2021-06"
        )
        bare = await _link(client, ids["ada vey"], "related", ids["bo lin"])
        itself = await _link(client, ids["acme"], "related", ids["acme"], note="x")
        unknown = await _link(client, ids["ada vey"], "related", uuid.uuid4(), note="x")
        first = await _link(client, ids["ada vey"], "member_of", ids["acme"], valid_from="2019")
        again = await _link(client, ids["ada vey"], "member_of", ids["acme"], valid_from="2019")
        # A second period of membership is a second link.
        second = await _link(client, ids["ada vey"], "member_of", ids["acme"], valid_from="2023")
        related = await _link(client, ids["bo lin"], "related", ids["ada vey"], note="Siblings")
        mirrored = await _link(client, ids["ada vey"], "related", ids["bo lin"], note="Again")

    assert part.status_code == 422 and "A person cannot be part of" in part.json()["detail"]
    assert (date.status_code, order.status_code, bare.status_code) == (422, 422, 422)
    assert (itself.status_code, unknown.status_code) == (422, 404)
    assert (first.status_code, again.status_code, second.status_code) == (201, 409, 201)
    assert (related.status_code, mirrored.status_code) == (201, 409)


async def test_cycle_in_succeeded_by_is_rejected() -> None:
    language = _language()
    ids = await _entities(language, ("ORG", "alpha"), ("ORG", "beta"), ("ORG", "gamma"))

    async with _client() as client:
        assert (await _link(client, ids["alpha"], "later_name", ids["beta"])).status_code == 201
        assert (await _link(client, ids["beta"], "later_name", ids["gamma"])).status_code == 201
        closing = await _link(client, ids["gamma"], "later_name", ids["alpha"])
        inverse = await _link(client, ids["alpha"], "earlier_name", ids["gamma"])

    assert (closing.status_code, inverse.status_code) == (409, 409)
    assert "cycle" in closing.json()["detail"]


async def test_cycle_in_part_of_is_rejected() -> None:
    language = _language()
    ids = await _entities(language, ("GPE", "crete"), ("GPE", "greece"))

    async with _client() as client:
        assert (await _link(client, ids["crete"], "part_of", ids["greece"])).status_code == 201
        assert (await _link(client, ids["crete"], "has_part", ids["greece"])).status_code == 409


async def test_edit_and_remove_are_recorded_in_the_history() -> None:
    language = _language()
    ids = await _entities(language, ("PERSON", "kai rho"), ("ORG", "union party"))
    async with session_factory() as db, db.begin():
        article_id = await _article(db, [])
        title = (await db.get(Article, article_id)).title  # type: ignore[union-attr]

    async with _client() as client:
        added = (
            await _link(
                client,
                ids["kai rho"],
                "leader_of",
                ids["union party"],
                source_article_id=str(article_id),
            )
        ).json()
        changed = await client.patch(
            f"/entity-relations/{added['id']}",
            json={"valid_from": "2015", "note": "Elected at the congress"},
            headers=CSRF,
        )
        seen = (await client.get(f"/entities/{ids['union party']}/see-also")).json()
        refused = await client.patch(
            f"/entity-relations/{added['id']}", json={"valid_to": "2014"}, headers=CSRF
        )
        removed = await client.delete(f"/entity-relations/{added['id']}", headers=CSRF)
        gone = await client.delete(f"/entity-relations/{added['id']}", headers=CSRF)
        history = (await client.get(f"/entities/{ids['kai rho']}/history")).json()
        after = (await client.get(f"/entities/{ids['union party']}/see-also")).json()

    assert added["source_article"] == {"id": str(article_id), "title": title}
    assert changed.status_code == 200
    assert seen["items"][0]["label"] == "led_by"
    assert (seen["items"][0]["valid_from"], seen["items"][0]["note"]) == (
        "2015",
        "Elected at the congress",
    )
    assert (refused.status_code, removed.status_code, gone.status_code) == (422, 204, 404)
    assert [item["action"] for item in history["items"]] == [
        "relation_added",
        "relation_changed",
        "relation_removed",
    ]
    assert after["items"] == []


async def test_merge_moves_relations_and_drops_duplicates() -> None:
    from app.entities.authority import merge
    from app.entities.relations import see_also

    language = _language()
    ids = await _entities(
        language,
        ("GPE", "hellas"),
        ("GPE", "greece"),
        ("ORG", "nato"),
        ("ORG", "eu"),
        ("GPE", "cyprus"),
    )
    async with _client() as client:
        await _link(client, ids["greece"], "member_of", ids["nato"], valid_from="1952")
        await _link(client, ids["hellas"], "member_of", ids["nato"], valid_from="1952")
        await _link(client, ids["hellas"], "member_of", ids["eu"], valid_from="1981")
        await _link(client, ids["cyprus"], "related", ids["hellas"], note="Neighbours")

    async with session_factory() as db, db.begin():
        await merge(db, variant_id=ids["hellas"], target_id=ids["greece"])
    async with session_factory() as db:
        kept = sorted(
            (item.label, item.entity.normalized_text, item.relation.valid_from)
            for item in await see_also(db, ids["greece"])
        )
        left = await see_also(db, ids["hellas"])

    assert kept == [
        ("member_of", "eu", "1981"),
        ("member_of", "nato", "1952"),
        ("related", "cyprus", None),
    ]
    # The variant's id now answers with its root's links.
    assert sorted(item.entity.normalized_text for item in left) == ["cyprus", "eu", "nato"]


async def test_merge_of_linked_entities_is_rejected() -> None:
    language = _language()
    ids = await _entities(language, ("ORG", "twitter"), ("ORG", "x corp"))

    async with _client() as client:
        await _link(client, ids["twitter"], "later_name", ids["x corp"])
        merged = await client.post(
            f"/entities/{ids['twitter']}/merge",
            json={"target_id": str(ids["x corp"])},
            headers=CSRF,
        )

    assert merged.status_code == 409
    assert "see-also" in merged.json()["detail"]


async def test_export_and_import_carry_the_relations() -> None:
    from app.entities.transfer import export_authorities, import_authorities

    source, target = _language(), _language()
    ids = await _entities(source, ("ORG", "old co"), ("ORG", "new co"), ("PERSON", "dee ash"))
    async with _client() as client:
        await _link(client, ids["old co"], "later_name", ids["new co"], valid_from="2020-05")
        await _link(client, ids["dee ash"], "related", ids["new co"], note="Founder")

    async with session_factory() as db:
        exported = await export_authorities(db, language=source)
    # By identity (language, type, name), so a related pair always reads the same way round.
    assert [
        (
            item["subject"]["normalized_text"],
            item["relation_type"],
            item["object"]["normalized_text"],
        )
        for item in exported["relations"]
    ] == [("new co", "related", "dee ash"), ("old co", "succeeded_by", "new co")]
    assert exported["relations"][1]["valid_from"] == "2020-05"
    data = json.loads(json.dumps(exported).replace(f'"{source}"', f'"{target}"'))

    async with session_factory() as db, db.begin():
        first = await import_authorities(db, data)
    async with session_factory() as db, db.begin():
        second = await import_authorities(db, data)

    assert (first.relations_added, second.relations_added) == (2, 0)
    assert first.conflicts == [] and second.conflicts == []
    async with session_factory() as db:
        new_co = await db.scalar(
            select(Entity).where(Entity.language == target, Entity.normalized_text == "new co")
        )
        assert new_co is not None
        from app.entities.relations import see_also

        assert sorted(
            (item.label, item.entity.normalized_text, item.relation.valid_from)
            for item in await see_also(db, new_co.id)
        ) == [("earlier_name", "old co", "2020-05"), ("related", "dee ash", None)]


async def test_downgrade_to_0019_removes_only_relations() -> None:
    language = _language()
    ids = await _entities(language, ("ORG", "keep co"), ("ORG", "next co"))
    async with _client() as client:
        await _link(client, ids["keep co"], "later_name", ids["next co"])
        await client.patch(
            f"/entities/{ids['keep co']}", json={"status": "established"}, headers=CSRF
        )
    async with session_factory() as db:
        entities_before = await db.scalar(select(func.count()).select_from(Entity))

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0019")
    try:
        async with session_factory() as db:
            assert await db.scalar(text("SELECT to_regclass('entity_relations') IS NULL")) is True
            actions = set(
                (
                    await db.scalars(
                        text("SELECT action FROM entity_authority_changes WHERE entity_id = :id"),
                        {"id": ids["keep co"]},
                    )
                ).all()
            )
            assert actions == {"status_changed"}
            assert await db.scalar(text("SELECT count(*) FROM nlp_entities")) == entities_before
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

    async with session_factory() as db:
        assert await db.scalar(text("SELECT to_regclass('entity_relations') IS NOT NULL")) is True
