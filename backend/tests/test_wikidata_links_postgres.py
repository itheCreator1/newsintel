"""Linking an authority root to a Wikidata item: only the user links, one QID per root."""

import os
import random
import uuid

import pytest
from sqlalchemy import select
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import CSRF, _client

from app.db.session import session_factory
from app.nlp.models import Entity, EntityAuthorityChange
from app.wikidata.cache import store_items
from app.wikidata.models import EntityExternalId, WikidataCandidate, WikidataRun
from app.wikidata.parsing import Item

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


# Linking adds an item's labels as names: a word of this run's own keeps them apart from the
# names other tests use.
RUN = uuid.uuid4().hex[:8]


def _qid() -> str:
    """A QID no other test uses."""
    return f"Q{random.randrange(10**9, 10**10)}"


async def _cache(*items: Item) -> None:
    async with session_factory() as db, db.begin():
        await store_items(db, items)


def _full(qid: str, label: str, **values: object) -> Item:
    return Item(
        qid=qid,
        state="ok",
        revision=11,
        labels={"en": f"{label} {RUN}"},
        descriptions={"en": f"{label}, for the tests"},
        instance_of=["Q5"],
        ids={"viaf": "4711", "lcnaf": "n0000001"},
        sitelinks=3,
        claims_fetched=True,
        **values,  # type: ignore[arg-type]
    )


async def _roots(language: str, *names: str, entity_type: str = "PERSON") -> dict[str, uuid.UUID]:
    async with session_factory() as db, db.begin():
        return {name: (await _named(db, language, entity_type, name)).id for name in names}


async def _external(entity_id: uuid.UUID) -> dict[str, tuple[str, str]]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(EntityExternalId).where(EntityExternalId.entity_id == entity_id)
        )
        return {row.scheme: (row.value, row.source) for row in rows}


async def _actions(entity_id: uuid.UUID) -> list[tuple[str, object]]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(EntityAuthorityChange)
            .where(EntityAuthorityChange.entity_id == entity_id)
            .order_by(EntityAuthorityChange.created_at)
        )
        return [(row.action, row.after or row.before) for row in rows]


async def test_linking_writes_the_qid_its_identifiers_and_the_history() -> None:
    language = _language()
    ids = await _roots(language, "ada lovelace")
    qid = _qid()
    await _cache(_full(qid, "Ada Lovelace"))

    async with _client() as client:
        linked = await client.post(
            f"/entities/{ids['ada lovelace']}/wikidata", json={"qid": qid}, headers=CSRF
        )
        assert linked.status_code == 200, linked.text
        shown = (await client.get(f"/entities/{ids['ada lovelace']}/wikidata")).json()

    assert await _external(ids["ada lovelace"]) == {
        "wikidata": (qid, "user"),
        "viaf": ("4711", "wikidata"),
        "lcnaf": ("n0000001", "wikidata"),
    }
    assert await _actions(ids["ada lovelace"]) == [("wikidata_linked", {"qid": qid})]
    assert shown["qid"] == qid
    assert shown["identifiers"] == {"viaf": "4711", "lcnaf": "n0000001"}
    assert shown["item"]["labels"] == {"en": f"Ada Lovelace {RUN}"}
    assert shown["item"]["descriptions"] == {"en": "Ada Lovelace, for the tests"}
    assert shown["item"]["state"] == "ok"
    assert shown["fetch_pending"] is False
    assert linked.json() == shown


async def test_a_variants_id_links_its_root_and_linking_again_changes_nothing() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "PERSON", "grace hopper")
        variant = await _named(db, language, "PERSON", "g. hopper", authority_id=root.id)
        root_id, variant_id = root.id, variant.id
    qid = _qid()
    await _cache(_full(qid, "Grace Hopper"))
    async with _client() as client:
        for _ in range(2):
            response = await client.post(
                f"/entities/{variant_id}/wikidata", json={"qid": qid}, headers=CSRF
            )
            assert response.status_code == 200, response.text
    assert (await _external(root_id))["wikidata"] == (qid, "user")
    assert await _external(variant_id) == {}
    assert [action for action, _ in await _actions(root_id)] == ["wikidata_linked"]


async def test_one_qid_belongs_to_one_root_and_the_refusal_names_it() -> None:
    language = _language()
    ids = await _roots(language, "alan turing", "a. m. turing")
    qid = _qid()
    await _cache(_full(qid, "Alan Turing"))
    async with _client() as client:
        await client.post(
            f"/entities/{ids['alan turing']}/wikidata", json={"qid": qid}, headers=CSRF
        )
        refused = await client.post(
            f"/entities/{ids['a. m. turing']}/wikidata", json={"qid": qid}, headers=CSRF
        )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["entity_id"] == str(ids["alan turing"])
    assert detail["display_name"] == "Alan Turing"
    assert qid in detail["message"]
    assert await _external(ids["a. m. turing"]) == {}


async def test_an_ambiguous_name_a_second_qid_and_a_bad_qid_are_refused() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        smith = await _named(db, language, "PERSON", "smith", ambiguous=True)
        jones = await _named(db, language, "PERSON", "jones")
        ids = (smith.id, jones.id)
    first, second = _qid(), _qid()
    async with _client() as client:
        ambiguous = await client.post(
            f"/entities/{ids[0]}/wikidata", json={"qid": first}, headers=CSRF
        )
        assert ambiguous.status_code == 409
        assert "ambiguous" in ambiguous.json()["detail"]["message"].lower()

        assert (
            await client.post(f"/entities/{ids[1]}/wikidata", json={"qid": first}, headers=CSRF)
        ).status_code == 200
        again = await client.post(
            f"/entities/{ids[1]}/wikidata", json={"qid": second}, headers=CSRF
        )
        assert again.status_code == 409
        assert first in again.json()["detail"]["message"]

        for bad in ("q42", "Q0", "P31", "Q42|Q43", ""):
            response = await client.post(
                f"/entities/{ids[1]}/wikidata", json={"qid": bad}, headers=CSRF
            )
            assert response.status_code == 422, bad

        missing = await client.post(
            f"/entities/{uuid.uuid4()}/wikidata", json={"qid": first}, headers=CSRF
        )
        assert missing.status_code == 404
        no_csrf = await client.post(f"/entities/{ids[1]}/wikidata", json={"qid": first})
        assert no_csrf.status_code == 403


async def test_an_item_not_fetched_in_full_is_linked_and_its_fetch_queued_once() -> None:
    language = _language()
    ids = await _roots(language, "mary somerville", "caroline herschel")
    light, unknown = _qid(), _qid()
    # A candidate's light fetch: names, no claims.
    await _cache(Item(qid=light, state="ok", revision=3, labels={"en": f"Mary Somerville {RUN}"}))
    async with _client() as client:
        first = await client.post(
            f"/entities/{ids['mary somerville']}/wikidata", json={"qid": light}, headers=CSRF
        )
        assert first.status_code == 200
        assert first.json()["fetch_pending"] is True
        assert first.json()["item"]["labels"] == {"en": f"Mary Somerville {RUN}"}
        # Typed by hand: nothing cached yet.
        typed = await client.post(
            f"/entities/{ids['caroline herschel']}/wikidata", json={"qid": unknown}, headers=CSRF
        )
        assert typed.status_code == 200
        assert typed.json()["item"] is None
        assert typed.json()["fetch_pending"] is True
        await client.delete(f"/entities/{ids['caroline herschel']}/wikidata", headers=CSRF)
        await client.post(
            f"/entities/{ids['caroline herschel']}/wikidata", json={"qid": unknown}, headers=CSRF
        )

    assert await _external(ids["mary somerville"]) == {"wikidata": (light, "user")}
    async with session_factory() as db:
        runs = list(
            await db.scalars(
                select(WikidataRun).where(
                    WikidataRun.entity_id.in_(list(ids.values())), WikidataRun.status == "queued"
                )
            )
        )
    assert sorted((run.kind, run.entity_id) for run in runs) == sorted(
        [("refresh", ids["mary somerville"]), ("refresh", ids["caroline herschel"])]
    )


async def test_a_redirected_item_links_its_target_and_a_deleted_one_is_refused() -> None:
    language = _language()
    ids = await _roots(language, "emmy noether", "sofia kovalevskaya")
    old, new, gone = _qid(), _qid(), _qid()
    await _cache(
        Item(qid=old, state="redirected", redirect_to=new),
        _full(new, "Emmy Noether"),
        Item(qid=gone, state="missing"),
    )
    async with _client() as client:
        moved = await client.post(
            f"/entities/{ids['emmy noether']}/wikidata", json={"qid": old}, headers=CSRF
        )
        deleted = await client.post(
            f"/entities/{ids['sofia kovalevskaya']}/wikidata", json={"qid": gone}, headers=CSRF
        )
    assert moved.status_code == 200
    assert moved.json()["qid"] == new
    assert deleted.status_code == 409
    assert gone in deleted.json()["detail"]["message"]


async def test_unlinking_removes_the_link_and_its_identifiers_but_keeps_names() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "PERSON", "lise meitner")
        name = await _named(
            db, language, "PERSON", "λιζε μαιτνερ", authority_id=root.id, name_source="wikidata"
        )
        root_id, name_id = root.id, name.id
    qid = _qid()
    await _cache(_full(qid, "Lise Meitner"))
    async with _client() as client:
        await client.post(f"/entities/{root_id}/wikidata", json={"qid": qid}, headers=CSRF)
        removed = await client.delete(f"/entities/{root_id}/wikidata", headers=CSRF)
        assert removed.status_code == 204
        again = await client.delete(f"/entities/{root_id}/wikidata", headers=CSRF)
        assert again.status_code == 409
        shown = (await client.get(f"/entities/{root_id}/wikidata")).json()
    assert await _external(root_id) == {}
    assert shown["qid"] is None
    assert shown["item"] is None
    assert [action for action, _ in await _actions(root_id)] == [
        "wikidata_linked",
        "wikidata_unlinked",
    ]
    async with session_factory() as db:
        kept = await db.get(Entity, name_id)
        assert kept is not None and kept.authority_id == root_id


async def test_linking_clears_the_roots_open_candidates() -> None:
    language = _language()
    ids = await _roots(language, "rosalind franklin")
    chosen, other = _qid(), _qid()
    await _cache(_full(chosen, "Rosalind Franklin"))
    async with session_factory() as db, db.begin():
        for qid in (chosen, other):
            db.add(
                WikidataCandidate(
                    entity_id=ids["rosalind franklin"], qid=qid, score=0.8, reasons=["label"]
                )
            )
    async with _client() as client:
        await client.post(
            f"/entities/{ids['rosalind franklin']}/wikidata", json={"qid": chosen}, headers=CSRF
        )
    async with session_factory() as db:
        left = await db.scalars(
            select(WikidataCandidate.qid).where(
                WikidataCandidate.entity_id == ids["rosalind franklin"]
            )
        )
        assert list(left) == []


async def test_two_roots_with_different_qids_do_not_merge() -> None:
    language = _language()
    ids = await _roots(language, "georgia country", "georgia state", entity_type="GPE")
    country, state = _qid(), _qid()
    await _cache(_full(country, "Georgia"), _full(state, "Georgia"))
    async with _client() as client:
        await client.post(
            f"/entities/{ids['georgia country']}/wikidata", json={"qid": country}, headers=CSRF
        )
        await client.post(
            f"/entities/{ids['georgia state']}/wikidata", json={"qid": state}, headers=CSRF
        )
        refused = await client.post(
            f"/entities/{ids['georgia state']}/merge",
            json={"target_id": str(ids["georgia country"])},
            headers=CSRF,
        )
    assert refused.status_code == 409
    assert "Wikidata" in refused.json()["detail"]
    async with session_factory() as db:
        state_root = await db.get(Entity, ids["georgia state"])
        assert state_root is not None and state_root.authority_id is None


async def test_a_merged_roots_qid_moves_to_the_root_and_a_split_leaves_it() -> None:
    from app.entities import authority

    language = _language()
    ids = await _roots(language, "marie curie", "maria sklodowska")
    qid = _qid()
    await _cache(_full(qid, "Marie Curie"))
    async with _client() as client:
        await client.post(
            f"/entities/{ids['maria sklodowska']}/wikidata", json={"qid": qid}, headers=CSRF
        )
        merged = await client.post(
            f"/entities/{ids['maria sklodowska']}/merge",
            json={"target_id": str(ids["marie curie"])},
            headers=CSRF,
        )
        assert merged.status_code == 202, merged.text
    assert await _external(ids["marie curie"]) == {
        "wikidata": (qid, "user"),
        "viaf": ("4711", "wikidata"),
        "lcnaf": ("n0000001", "wikidata"),
    }
    assert await _external(ids["maria sklodowska"]) == {}
    assert ("wikidata_linked", {"qid": qid, "moved_from": str(ids["maria sklodowska"])}) in (
        await _actions(ids["marie curie"])
    )

    async with session_factory() as db, db.begin():
        await authority.split(db, ids["maria sklodowska"])
    assert (await _external(ids["marie curie"]))["wikidata"] == (qid, "user")
    assert await _external(ids["maria sklodowska"]) == {}


async def test_an_import_does_not_join_names_wikidata_keeps_apart() -> None:
    from app.entities import authority

    language = _language()
    async with session_factory() as db, db.begin():
        root = await _named(db, language, "ORG", "acme one")
        loose = await _named(db, language, "ORG", "acme two")
        db.add(EntityExternalId(entity_id=root.id, scheme="wikidata", value=_qid()))
        db.add(EntityExternalId(entity_id=loose.id, scheme="wikidata", value=_qid()))
        ids = (root.id, loose.id)
    with pytest.raises(authority.AuthorityError) as refused:
        async with session_factory() as db, db.begin():
            await authority.adopt(db, variant_id=ids[1], root_id=ids[0])
    assert refused.value.status_code == 409
