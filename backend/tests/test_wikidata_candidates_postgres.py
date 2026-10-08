"""Wikidata suggests items for roots; the user approves. Searches and classes are asked once.

Every test runs on the fake api.php of wikidata_fake.py, with a throttle on a fake clock.
"""

import asyncio
import os
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text, update
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article
from wikidata_fake import FakeWikidata, client, entity, settings

from app.db.session import session_factory
from app.nlp.models import Entity
from app.wikidata import runs
from app.wikidata.candidates import find_candidates
from app.wikidata.models import (
    EntityExternalId,
    WikidataCandidate,
    WikidataRun,
    WikidataSearch,
    WikidataThrottle,
)
from app.wikidata.names import name_key

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _qid() -> str:
    """A QID no other test uses."""
    return f"Q{random.randrange(10**9, 10**10)}"


def _word() -> str:
    return "x" + uuid.uuid4().hex[:8]


async def _root(
    name: str,
    *,
    entity_type: str = "PERSON",
    language: str = "en",
    articles: int = 0,
    **values: object,
) -> uuid.UUID:
    async with session_factory() as db, db.begin():
        root = Entity(
            language=language,
            entity_type=entity_type,
            normalized_text=name_key(entity_type, language, name),
            display_text=name,
            **values,
        )
        db.add(root)
        await db.flush()
        for _ in range(articles):
            await _article(db, [(root, [0])])
        return root.id


async def _candidates(root_id: uuid.UUID) -> dict[str, tuple[float, list[str], bool, bool]]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(WikidataCandidate).where(WikidataCandidate.entity_id == root_id)
        )
        return {row.qid: (row.score, row.reasons, row.exact, row.dismissed) for row in rows}


async def _find(fake: FakeWikidata, *root_ids: uuid.UUID, **config: object) -> None:
    options = settings(**config)
    async with client(fake, options) as wikidata:
        await find_candidates(wikidata, list(root_ids), options)


async def _no_runs() -> None:
    """Runs are bookkeeping: each test here starts with none, so a sweep is its own."""
    async with session_factory() as db, db.begin():
        await db.execute(text("DELETE FROM wikidata_runs"))
        await db.execute(text("DELETE FROM wikidata_throttle"))


def _person(qid: str, label: str, **values: object) -> dict[str, object]:
    return entity(qid, labels={"en": label}, instance_of=("Q5",), **values)  # type: ignore[arg-type]


async def test_a_root_gets_scored_candidates_and_nothing_is_linked() -> None:
    name = f"Kallisto {_word()}"
    root_id = await _root(name)
    exact, alias, other = _qid(), _qid(), _qid()
    fake = FakeWikidata()
    fake.add(
        _person(exact, name, sitelinks=10, descriptions={"en": "a poet"}),
        _person(alias, f"Kallisto Mavros {_word()}", aliases={"en": [name]}),
        _person(other, f"{name} Foundation"),
    )
    fake.searches[("en", name)] = [exact, alias, other]

    await _find(fake, root_id)

    found = await _candidates(root_id)
    assert set(found) == {exact, alias}
    score, reasons, is_exact, dismissed = found[exact]
    assert reasons[:2] == ["label:en", "type_matches"] and reasons[2] == "sitelinks:10"
    assert 0.8 < score < 0.9 and is_exact and not dismissed
    assert found[alias][1] == ["alias:en"] and not found[alias][2]
    # One search, one light fetch for all three, and the type of the exact label only.
    assert fake.actions("wbsearchentities") == [f"en:{name}"]
    assert [ids.split("|") for ids in fake.actions("wbgetentities")] == [[exact, alias, other]]
    assert fake.actions("wbgetclaims") == [f"{exact}:P31"]
    async with session_factory() as db:
        assert (
            await db.scalar(select(EntityExternalId).where(EntityExternalId.entity_id == root_id))
            is None
        )


async def test_a_search_waits_thirty_days_and_items_and_types_are_kept() -> None:
    name = f"Ioanna {_word()}"
    root_id = await _root(name)
    qid = _qid()
    fake = FakeWikidata()
    fake.add(_person(qid, name))
    fake.searches[("en", name)] = [qid]
    await _find(fake, root_id)
    first = len(fake.requests)

    await _find(fake, root_id)
    assert len(fake.requests) == first

    async with session_factory() as db, db.begin():
        await db.execute(
            update(WikidataSearch)
            .where(WikidataSearch.text == name)
            .values(fetched_at=datetime.now(UTC) - timedelta(days=31))
        )
    await _find(fake, root_id)
    # Only the search is asked again: the item and its type are still in the cache.
    assert fake.requests[first:] == [("wbsearchentities", f"en:{name}")]
    assert set(await _candidates(root_id)) == {qid}


async def test_a_search_that_found_nothing_is_kept_too() -> None:
    name = f"Nobody {_word()}"
    root_id = await _root(name)
    fake = FakeWikidata()
    await _find(fake, root_id)
    await _find(fake, root_id)
    assert fake.actions("wbsearchentities") == [f"en:{name}"]
    assert await _candidates(root_id) == {}


async def test_the_other_languages_name_is_searched_and_counts() -> None:
    word = _word()
    greek = f"Τσίπρας {word}"
    english = f"Alexis Tsipras {word}"
    root_id = await _root(greek, language="el")
    async with session_factory() as db, db.begin():
        db.add(
            Entity(
                language="en",
                entity_type="PERSON",
                normalized_text=name_key("PERSON", "en", english),
                display_text=english,
                authority_id=root_id,
            )
        )
    qid = _qid()
    fake = FakeWikidata()
    fake.add(
        entity(
            qid,
            labels={"el": f"Αλέξης Τσίπρας {word}", "en": english},
            aliases={"el": [greek]},
            instance_of=("Q5",),
        )
    )
    fake.searches[("en", english)] = [qid]

    await _find(fake, root_id)

    assert fake.actions("wbsearchentities") == [f"el:{greek}", f"en:{english}"]
    score, reasons, _, _ = (await _candidates(root_id))[qid]
    assert reasons == ["alias:el", "variant:en"]
    assert score == 0.6


async def test_a_dismissed_candidate_never_comes_back() -> None:
    name = f"Petros {_word()}"
    root_id = await _root(name)
    qid = _qid()
    fake = FakeWikidata()
    fake.add(_person(qid, name))
    fake.searches[("en", name)] = [qid]
    await _find(fake, root_id)

    async with _client() as api:
        response = await api.post(
            f"/entities/{root_id}/wikidata/candidates/{qid}/dismiss", headers=CSRF
        )
        unknown = await api.post(
            f"/entities/{root_id}/wikidata/candidates/{_qid()}/dismiss", headers=CSRF
        )
    assert response.status_code == 204, response.text
    assert unknown.status_code == 404

    async with session_factory() as db, db.begin():
        await db.execute(delete(WikidataSearch).where(WikidataSearch.text == name))
    await _find(fake, root_id)
    assert (await _candidates(root_id))[qid][3] is True
    async with _client() as api:
        shown = (await api.get(f"/entities/{root_id}/wikidata")).json()
    assert shown["candidates"] == []


async def test_an_item_another_root_holds_is_not_offered() -> None:
    name = f"Eleni {_word()}"
    root_id = await _root(name)
    holder_id = await _root(f"Eleni Holder {_word()}")
    held, free = _qid(), _qid()
    async with session_factory() as db, db.begin():
        db.add(EntityExternalId(entity_id=holder_id, scheme="wikidata", value=held))
    fake = FakeWikidata()
    fake.add(_person(held, name), _person(free, name))
    fake.searches[("en", name)] = [held, free]

    await _find(fake, root_id)

    assert set(await _candidates(root_id)) == {free}
    assert fake.actions("wbgetentities") == [free]


async def test_close_calls_climb_the_class_tree_once() -> None:
    word = _word()
    name = f"Georgia {word}"
    first_id = await _root(name, entity_type="GPE")
    second_id = await _root(f"Georgia Two {word}", entity_type="GPE")
    country, state = _qid(), _qid()
    sovereign, federated, region = _qid(), _qid(), _qid()
    fake = FakeWikidata()
    fake.add(
        entity(country, labels={"en": name}, instance_of=(sovereign,), sitelinks=300),
        entity(state, labels={"en": name}, instance_of=(federated,), sitelinks=200),
        entity(sovereign, labels={"en": "sovereign state"}, subclass_of=("Q6256",)),
        entity(federated, labels={"en": "federated state"}, subclass_of=(region,)),
        entity(region, labels={"en": "region"}),
        entity(_qid(), labels={"en": f"Georgia Two {word}"}, instance_of=(sovereign,)),
    )
    fake.searches[("en", name)] = [country, state]

    await _find(fake, first_id)

    found = await _candidates(first_id)
    assert "type_matches" in found[country][1] and "type_differs" in found[state][1]
    # Two items carry the label: neither is approved in bulk.
    assert not found[country][2] and not found[state][2]
    assert sorted(fake.actions("wbgetclaims")) == sorted([f"{country}:P31", f"{state}:P31"])
    # The classes are fetched with their claims, after a size check.
    class_fetches = [
        ids for ids in fake.actions("wbgetentities") if sovereign in ids or region in ids
    ]
    assert class_fetches

    other = next(
        qid for qid in fake.entities if qid not in {country, state, sovereign, federated, region}
    )
    fake.searches[("en", f"Georgia Two {word}")] = [other]
    before = len(fake.requests)
    await _find(fake, second_id)
    # Its class is already known: a search, the item and its type, nothing more.
    assert [kind for kind, _ in fake.requests[before:]] == [
        "wbsearchentities",
        "wbgetentities",
        "wbgetclaims",
    ]
    assert "type_matches" in (await _candidates(second_id))[other][1]


async def test_approving_the_exact_ones_links_only_the_clear_ones() -> None:
    word = _word()
    clear = f"Clear {word}"
    shared = f"Shared {word}"
    clear_id = await _root(clear)
    shared_id = await _root(shared)
    one, two, three = _qid(), _qid(), _qid()
    fake = FakeWikidata()
    fake.add(
        _person(one, clear, descriptions={"en": "the clear one"}),
        _person(two, shared),
        _person(three, shared),
    )
    fake.searches[("en", clear)] = [one]
    fake.searches[("en", shared)] = [two, three]
    await _find(fake, clear_id, shared_id)

    async with _client() as api:
        queue = await api.get("/wikidata/candidates", params={"min_score": 0.5, "limit": 200})
        approved = await api.post("/wikidata/candidates/approve-exact", headers=CSRF)
        shown = (await api.get(f"/entities/{clear_id}/wikidata")).json()
    assert queue.status_code == 200, queue.text
    mine = [
        row for row in queue.json()["items"] if row["entity_id"] in {str(clear_id), str(shared_id)}
    ]
    assert {(row["qid"], row["exact"]) for row in mine} == {
        (one, True),
        (two, False),
        (three, False),
    }
    first = next(row for row in mine if row["qid"] == one)
    assert first["label"] == clear and first["description"] == "the clear one"
    assert first["display_name"] == clear and first["entity_type"] == "PERSON"

    assert approved.status_code == 200, approved.text
    assert approved.json()["linked"] >= 1
    assert shown["qid"] == one
    async with session_factory() as db:
        assert (
            await db.scalar(
                select(EntityExternalId.value).where(
                    EntityExternalId.entity_id == shared_id, EntityExternalId.scheme == "wikidata"
                )
            )
            is None
        )
    # A link spends the root's other candidates; the shared root keeps its two.
    assert await _candidates(clear_id) == {}
    assert set(await _candidates(shared_id)) == {two, three}


async def test_the_find_button_queues_one_search_and_the_page_shows_it() -> None:
    await _no_runs()
    name = f"Button {_word()}"
    root_id = await _root(name)
    linked_id = await _root(f"Linked {_word()}")
    async with session_factory() as db, db.begin():
        db.add(EntityExternalId(entity_id=linked_id, scheme="wikidata", value=_qid()))

    async with _client() as api:
        first = await api.post(f"/entities/{root_id}/wikidata/search", headers=CSRF)
        again = await api.post(f"/entities/{root_id}/wikidata/search", headers=CSRF)
        linked = await api.post(f"/entities/{linked_id}/wikidata/search", headers=CSRF)
        missing = await api.post(f"/entities/{uuid.uuid4()}/wikidata/search", headers=CSRF)
        pending = (await api.get(f"/entities/{root_id}/wikidata")).json()
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued" and first.json()["kind"] == "candidates"
    assert again.json()["id"] == first.json()["id"]
    assert linked.status_code == 409 and missing.status_code == 404
    assert pending["search_pending"] is True and pending["candidates"] == []

    qid = _qid()
    fake = FakeWikidata()
    fake.add(_person(qid, name, descriptions={"en": "found by the button"}))
    fake.searches[("en", name)] = [qid]
    options = settings()
    async with session_factory() as db, db.begin():
        claimed = await runs.claim_run(db, options)
    assert claimed is not None and claimed[0] == uuid.UUID(first.json()["id"])
    async with client(fake, options) as wikidata:
        await runs.process_run(claimed[0], claimed[1], wikidata, options)

    async with _client() as api:
        shown = (await api.get(f"/entities/{root_id}/wikidata")).json()
    assert shown["search_pending"] is False
    assert [(row["qid"], row["label"], row["description"]) for row in shown["candidates"]] == [
        (qid, name, "found by the button")
    ]
    async with session_factory() as db:
        run = await db.get(WikidataRun, claimed[0])
        assert run is not None
        assert (run.status, run.checked, run.requests) == ("finished", 1, len(fake.requests))
        assert run.started_at is not None and run.finished_at is not None


async def test_the_sweep_takes_rooted_names_with_enough_articles_only() -> None:
    await _no_runs()
    word = _word()
    names = {
        "eligible": dict(articles=3),
        "few": dict(articles=2),
        "ambiguous": dict(articles=3, ambiguous=True),
        "linked": dict(articles=3),
        "misc": dict(articles=3, entity_type="MISC"),
    }
    ids = {key: await _root(f"{key} {word}", **values) for key, values in names.items()}  # type: ignore[arg-type]
    async with session_factory() as db, db.begin():
        db.add(EntityExternalId(entity_id=ids["linked"], scheme="wikidata", value=_qid()))

    options = settings()
    async with session_factory() as db, db.begin():
        sweep = await runs.ensure_sweep(db, options)
        assert sweep is not None
        assert await runs.ensure_sweep(db, options) is None
    fake = FakeWikidata()
    while True:
        async with session_factory() as db, db.begin():
            claimed = await runs.claim_run(db, options)
        if claimed is None:
            break
        async with client(fake, options) as wikidata:
            await runs.process_run(claimed[0], claimed[1], wikidata, options)

    searched = {detail.split(":", 1)[1] for detail in fake.actions("wbsearchentities")}
    assert f"eligible {word}" in searched
    assert not {f"{key} {word}" for key in ("few", "ambiguous", "linked", "misc")} & searched
    async with session_factory() as db:
        run = await db.get(WikidataRun, sweep.id)
        assert run is not None and run.status == "finished"
        # A finished sweep is not repeated until a day has passed.
    async with session_factory() as db, db.begin():
        assert await runs.ensure_sweep(db, options) is None


async def test_a_sweep_stopped_by_the_budget_waits_and_resumes() -> None:
    await _no_runs()
    names = [f"Budget {_word()}", f"Budget {_word()}"]
    for name in names:
        await _root(name, articles=3)
    fake = FakeWikidata()
    tight = settings(wikidata_daily_request_budget=1)
    async with session_factory() as db, db.begin():
        sweep = await runs.ensure_sweep(db, tight)
        assert sweep is not None
        claimed = await runs.claim_run(db, tight)
    assert claimed is not None
    async with client(fake, tight) as wikidata:
        await runs.process_run(claimed[0], claimed[1], wikidata, tight)
    async with session_factory() as db:
        run = await db.get(WikidataRun, sweep.id)
        assert run is not None
        assert run.status == "queued" and run.claim_token is None
        assert run.error is not None and "budget" in run.error.lower()
    assert len(fake.requests) == 1

    options = settings()
    while True:
        async with session_factory() as db, db.begin():
            claimed = await runs.claim_run(db, options)
        if claimed is None:
            break
        async with client(fake, options) as wikidata:
            await runs.process_run(claimed[0], claimed[1], wikidata, options)
    async with session_factory() as db:
        run = await db.get(WikidataRun, sweep.id)
        assert run is not None and run.status == "finished" and run.error is None
    searched = fake.actions("wbsearchentities")
    assert [searched.count(f"en:{name}") for name in names] == [1, 1]


async def test_without_a_network_the_run_fails_and_nothing_changes() -> None:
    await _no_runs()
    name = f"Offline {_word()}"
    root_id = await _root(name)
    kept = _qid()
    async with session_factory() as db, db.begin():
        db.add(WikidataCandidate(entity_id=root_id, qid=kept, score=0.6, reasons=["label:en"]))
        run = await runs.request_search(db, root_id)
    fake = FakeWikidata()

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    fake.failure = offline
    options = settings()
    async with session_factory() as db, db.begin():
        claimed = await runs.claim_run(db, options)
    assert claimed is not None and claimed[0] == run.id
    async with client(fake, options) as wikidata:
        await runs.process_run(claimed[0], claimed[1], wikidata, options)

    async with session_factory() as db:
        failed = await db.get(WikidataRun, run.id)
        assert failed is not None
        assert (failed.status, failed.errors) == ("failed", 1)
        assert failed.error is not None and "ConnectError" in failed.error
    assert set(await _candidates(root_id)) == {kept}
    assert len(fake.requests) == 1


async def test_runs_go_one_at_a_time_buttons_first_and_a_lost_one_is_taken_again() -> None:
    await _no_runs()
    options = settings()
    root_id = await _root(f"Order {_word()}")
    async with session_factory() as db, db.begin():
        sweep = await runs.ensure_sweep(db, options)
        button = await runs.request_search(db, root_id)
    assert sweep is not None
    async with session_factory() as db, db.begin():
        first = await runs.claim_run(db, options)
    async with session_factory() as db, db.begin():
        busy = await runs.claim_run(db, options)
    assert first is not None and first[0] == button.id
    assert busy is None

    async with session_factory() as db, db.begin():
        await db.execute(
            update(WikidataRun)
            .where(WikidataRun.id == button.id)
            .values(claim_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    async with session_factory() as db, db.begin():
        again = await runs.claim_run(db, options)
    assert again is not None and again[0] == button.id and again[1] != first[1]

    # The worker that lost its claim finds it gone and touches nothing.
    fake = FakeWikidata()
    async with client(fake, options) as wikidata:
        await runs.process_run(first[0], first[1], wikidata, options)
    assert fake.requests == []
    async with session_factory() as db:
        current = await db.get(WikidataRun, button.id)
        assert current is not None and current.status == "running"


async def test_nothing_is_dispatched_while_wikidata_is_paused() -> None:
    await _no_runs()
    options = settings()
    root_id = await _root(f"Paused {_word()}")
    async with session_factory() as db, db.begin():
        await runs.request_search(db, root_id)
        db.add(
            WikidataThrottle(
                id=1,
                paused_until=datetime.now(UTC) + timedelta(minutes=10),
                pause_reason="rate_limited",
            )
        )
    async with session_factory() as db, db.begin():
        assert await runs.claim_run(db, options) is None
    await _no_runs()


async def test_the_scheduler_step_queues_a_sweep_and_hands_one_run_to_a_worker() -> None:
    await _no_runs()
    sent: list[tuple[str, str]] = []

    def send(run_id: str, token: str) -> None:
        sent.append((run_id, token))

    # The test settings have no contact: Wikidata is off and nothing is queued.
    assert await runs.schedule_wikidata(send=send) == 0
    async with session_factory() as db:
        assert await db.scalar(select(WikidataRun.id).limit(1)) is None

    assert await runs.schedule_wikidata(settings(), send=send) == 1
    assert await runs.schedule_wikidata(settings(), send=send) == 0
    async with session_factory() as db:
        sweep = await db.scalar(select(WikidataRun))
        assert sweep is not None and sweep.entity_id is None and sweep.status == "running"
    assert sent == [(str(sweep.id), str(sweep.claim_token))]
    await _no_runs()


async def _columns(table: str) -> set[str]:
    async with session_factory() as db:
        rows = await db.scalars(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :table"),
            {"table": table},
        )
        return set(rows)


async def test_migration_0022_adds_claims_and_the_exact_flag_and_goes_back() -> None:
    root_id = await _root(f"Kept {_word()}")
    qid = _qid()
    async with session_factory() as db, db.begin():
        db.add(WikidataCandidate(entity_id=root_id, qid=qid, score=0.6, reasons=["label:en"]))
    assert {"claim_token", "claim_expires_at"} <= await _columns("wikidata_runs")
    assert "exact" in await _columns("wikidata_candidates")

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0021")
    try:
        assert not {"claim_token", "claim_expires_at"} & await _columns("wikidata_runs")
        assert "exact" not in await _columns("wikidata_candidates")
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    # The candidates themselves are kept; only the flag is worked out again.
    assert set(await _candidates(root_id)) == {qid}
