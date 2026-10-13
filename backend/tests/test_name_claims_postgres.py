"""A root claims every type of one of its names, now and for the names NER finds later (0026)."""

import json
import os
import uuid

import pytest
from sqlalchemy import select
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _finish
from test_nlp_jobs_postgres import ENGLISH_DESCRIPTION, _entity_row, _run_entities_job

from app.db.session import session_factory
from app.entities.authority import AuthorityError, claim_name, merge, release_name
from app.nlp.models import Entity, EntityAuthorityChange, EntityDistinct, EntityNameClaim

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _root_of(entity_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as db:
        entity = await db.get(Entity, entity_id)
        assert entity is not None
        return entity.authority_id or entity.id


async def _finish_all(runs: list) -> None:  # type: ignore[type-arg]
    for run in runs:
        await _finish(run)


async def test_a_claim_brings_every_root_of_that_name_and_reports_what_it_could_not() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        org = await _named(db, language, "ORG", "ai")
        person = await _named(db, language, "PERSON", "ai")
        product = await _named(db, language, "PRODUCT", "ai")
        # The user said this one is different: the claim leaves it alone.
        event = await _named(db, language, "EVENT", "ai")
        low, high = sorted((org.id, event.id))
        db.add(EntityDistinct(a_id=low, b_id=high))
        # Already a name of another root: reported, not taken.
        other = await _named(db, language, "ORG", "allen institute")
        held = await _named(db, language, "OTHER", "ai", authority_id=other.id)
        ids = org.id, person.id, product.id, event.id, held.id

    async with session_factory() as db, db.begin():
        result = await claim_name(db, ids[0])
        runs = [run.id for run in result.runs]
        skipped = {item.entity_id: item.reason for item in result.skipped}
    await _finish_all(runs)

    assert len(runs) == 2
    assert [await _root_of(value) for value in ids[1:3]] == [ids[0], ids[0]]
    assert await _root_of(ids[3]) == ids[3]
    assert set(skipped) == {ids[3], ids[4]}
    assert skipped[ids[3]] == "These entities are marked as different"
    assert "another entity" in skipped[ids[4]]
    async with session_factory() as db:
        claim = await db.get(EntityNameClaim, (language, "ai"))
        assert claim is not None and claim.root_id == ids[0]
        actions = list(
            await db.scalars(
                select(EntityAuthorityChange.action).where(
                    EntityAuthorityChange.entity_id == ids[0],
                    EntityAuthorityChange.action == "claim_added",
                )
            )
        )
    assert actions == ["claim_added"]


async def test_a_root_may_claim_one_of_its_other_names() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "ORG", "artificial intelligence", preferred_type="TOPIC")
        await _named(db, language, "ORG", "ai", authority_id=root.id)
        person = await _named(db, language, "PERSON", "ai")
        root_id, person_id = root.id, person.id

    async with session_factory() as db, db.begin():
        result = await claim_name(db, root_id, "ai")
        runs = [run.id for run in result.runs]
    await _finish_all(runs)

    assert await _root_of(person_id) == root_id
    async with session_factory() as db:
        claim = await db.get(EntityNameClaim, (language, "ai"))
        assert claim is not None and claim.root_id == root_id


async def test_a_claim_is_refused_for_a_variant_an_ambiguous_root_or_a_foreign_name() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "PERSON", "jordan", ambiguous=True)
        plain = await _named(db, language, "ORG", "acme")
        variant = await _named(db, language, "ORG", "acme ltd", authority_id=plain.id)
        rival = await _named(db, language, "PRODUCT", "acme")
        low, high = sorted((plain.id, rival.id))
        db.add(EntityDistinct(a_id=low, b_id=high))
        ids = root.id, plain.id, variant.id, rival.id

    async with session_factory() as db, db.begin():
        with pytest.raises(AuthorityError) as ambiguous:
            await claim_name(db, ids[0])
        with pytest.raises(AuthorityError) as as_variant:
            await claim_name(db, ids[2])
        with pytest.raises(AuthorityError) as foreign:
            await claim_name(db, ids[1], "jordan")
    assert (ambiguous.value.status_code, as_variant.value.status_code) == (409, 409)
    assert foreign.value.status_code == 422

    async with session_factory() as db, db.begin():
        await claim_name(db, ids[1])
    async with session_factory() as db, db.begin():
        # Kept apart by "not the same", the rival may not claim the name acme holds.
        with pytest.raises(AuthorityError) as taken:
            await claim_name(db, ids[3])
    assert taken.value.status_code == 409


async def test_a_name_ner_finds_later_under_a_new_type_joins_the_claiming_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = uuid.uuid4().hex[:8]
    async with session_factory() as db, db.begin():
        root = Entity(
            language="en",
            entity_type="ORG",
            normalized_text=f"orrin labs {token}",
            display_text=f"Orrin Labs {token}",
        )
        db.add(root)
        await db.flush()
        root_id = root.id
    async with session_factory() as db, db.begin():
        await claim_name(db, root_id)

    _, article_id, _ = await _run_entities_job(
        monkeypatch,
        title=f"Orrin Labs {token} announced a new wage plan for public sector workers today",
        description=ENGLISH_DESCRIPTION,
        spans=[(f"Orrin Labs {token}", "PERSON")],
    )

    (row,) = await _entity_row(article_id)
    assert row[0] == root_id and row[1] is not None
    async with session_factory() as db:
        joined = await db.get(Entity, row[1])
        assert joined is not None
        assert (joined.entity_type, joined.authority_id) == ("PERSON", root_id)
        recorded = await db.scalar(
            select(EntityAuthorityChange.after).where(
                EntityAuthorityChange.entity_id == joined.id,
                EntityAuthorityChange.action == "merged",
            )
        )
    assert recorded == {"authority_id": str(root_id), "by": "claim"}


async def test_a_merged_root_hands_its_claims_to_its_new_root_and_a_release_ends_them() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        first = await _named(db, language, "ORG", "ai lab")
        second = await _named(db, language, "ORG", "ai laboratory")
        first_id, second_id = first.id, second.id
    async with session_factory() as db, db.begin():
        await claim_name(db, first_id)
    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=first_id, target_id=second_id)
        run_id = run.id
    await _finish(run_id)
    async with session_factory() as db:
        claim = await db.get(EntityNameClaim, (language, "ai lab"))
        assert claim is not None and claim.root_id == second_id

    async with session_factory() as db, db.begin():
        assert await release_name(db, second_id, "ai lab")
        assert not await release_name(db, second_id, "ai lab")
    async with session_factory() as db:
        assert await db.get(EntityNameClaim, (language, "ai lab")) is None


async def test_the_api_claims_releases_and_the_dossier_shows_claims_and_other_types() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "ORG", "deepmind")
        person = await _named(db, language, "PERSON", "deepmind")
        root_id, person_id = root.id, person.id

    async with _client() as client:
        before = (await client.get(f"/entities/{root_id}")).json()
        claimed = await client.post(f"/entities/{root_id}/claims", json={}, headers=CSRF)
        after = (await client.get(f"/entities/{root_id}")).json()
        released = await client.request(
            "DELETE",
            f"/entities/{root_id}/claims",
            json={"normalized_text": "deepmind"},
            headers=CSRF,
        )
        missing = await client.request(
            "DELETE",
            f"/entities/{root_id}/claims",
            json={"normalized_text": "deepmind"},
            headers=CSRF,
        )

    assert [(item["id"], item["entity_type"]) for item in before["other_types"]] == [
        (str(person_id), "PERSON")
    ]
    assert before["claims"] == []
    assert claimed.status_code == 200
    body = claimed.json()
    assert (len(body["runs"]), body["skipped"]) == (1, [])
    assert after["claims"] == ["deepmind"]
    assert (released.status_code, missing.status_code) == (204, 404)


async def test_a_claim_travels_in_the_file() -> None:
    from app.entities.transfer import export_authorities, import_authorities

    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "ORG", "openai")
        root_id = root.id
    async with session_factory() as db, db.begin():
        await claim_name(db, root_id)
    async with session_factory() as db:
        exported = await export_authorities(db, language=language)
    (record,) = exported["entities"]
    assert record["claims"] == ["openai"]

    other = _language()
    data = json.loads(json.dumps(exported).replace(f'"{language}"', f'"{other}"'))
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)
    assert not report.conflicts
    async with session_factory() as db:
        claim = await db.get(EntityNameClaim, (other, "openai"))
        assert claim is not None
