"""Linked items stay current: revisions checked monthly, only changed items fetched again.

A redirect moves the link (with history), a deleted item keeps it with a warning, and nothing
changes when Wikidata cannot be reached. Every test runs on the fake api.php of wikidata_fake.py.
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
from sqlalchemy import select, text, update
from test_entity_authority_api_postgres import CSRF, _client
from wikidata_fake import FakeWikidata, client, entity, settings

from app.db.session import session_factory
from app.nlp.models import Entity, EntityAuthorityChange
from app.wikidata import runs
from app.wikidata.cache import store_items
from app.wikidata.models import EntityExternalId, WikidataItem, WikidataRun, WikidataThrottle
from app.wikidata.names import name_key
from app.wikidata.parsing import Item

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _qid() -> str:
    return f"Q{random.randrange(10**9, 10**10)}"


def _word() -> str:
    return "x" + uuid.uuid4().hex[:8]


async def _no_runs() -> None:
    async with session_factory() as db, db.begin():
        await db.execute(text("DELETE FROM wikidata_runs"))
        await db.execute(text("DELETE FROM wikidata_throttle"))


async def _root(name: str, *, entity_type: str = "PERSON", language: str = "en") -> uuid.UUID:
    async with session_factory() as db, db.begin():
        root = Entity(
            language=language,
            entity_type=entity_type,
            normalized_text=name_key(entity_type, language, name),
            display_text=name,
        )
        db.add(root)
        await db.flush()
        return root.id


async def _linked(name: str, qid: str, *, revision: int = 100, days_ago: float = 31) -> uuid.UUID:
    """A root linked to `qid`, whose full item the cache holds, last checked `days_ago`."""
    root_id = await _root(name)
    async with session_factory() as db, db.begin():
        db.add(EntityExternalId(entity_id=root_id, scheme="wikidata", value=qid))
        await store_items(
            db,
            [
                Item(
                    qid=qid,
                    state="ok",
                    revision=revision,
                    labels={"en": name},
                    instance_of=["Q5"],
                    claims_fetched=True,
                )
            ],
        )
        await db.execute(
            update(WikidataItem)
            .where(WikidataItem.qid == qid)
            .values(checked_at=datetime.now(UTC) - timedelta(days=days_ago))
        )
    return root_id


async def _work(fake: FakeWikidata, **config: object) -> list[uuid.UUID]:
    """Hand out runs and do them until none is left; returns the runs done."""
    options = settings(**config)
    done = []
    while True:
        async with session_factory() as db, db.begin():
            claimed = await runs.claim_run(db, options)
        if claimed is None:
            return done
        async with client(fake, options) as wikidata:
            await runs.process_run(claimed[0], claimed[1], wikidata, options)
        done.append(claimed[0])


async def _link_value(root_id: uuid.UUID) -> str | None:
    async with session_factory() as db:
        return await db.scalar(
            select(EntityExternalId.value).where(
                EntityExternalId.entity_id == root_id, EntityExternalId.scheme == "wikidata"
            )
        )


async def _history(root_id: uuid.UUID) -> list[tuple[str, object, object]]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(EntityAuthorityChange)
            .where(
                EntityAuthorityChange.entity_id == root_id,
                EntityAuthorityChange.action.like("wikidata_%"),
            )
            .order_by(EntityAuthorityChange.created_at)
        )
        return [(row.action, row.before, row.after) for row in rows]


def _person(qid: str, label: str, **values: object) -> dict[str, object]:
    return entity(qid, labels={"en": label}, instance_of=("Q5",), **values)  # type: ignore[arg-type]


async def test_a_new_link_is_fetched_in_full_and_brings_its_labels() -> None:
    await _no_runs()
    word = _word()
    name = f"Fresh {word}"
    root_id = await _root(name)
    qid = _qid()
    fake = FakeWikidata()
    fake.add(_person(qid, name))
    fake.entities[qid]["labels"]["el"] = {"language": "el", "value": f"Φρέσκος {word}"}
    fake.entities[qid]["claims"]["P214"] = [
        {
            "mainsnak": {
                "snaktype": "value",
                "property": "P214",
                "datavalue": {"value": "12345", "type": "string"},
            },
            "type": "statement",
            "rank": "normal",
        }
    ]

    async with _client() as api:
        linked = (
            await api.post(f"/entities/{root_id}/wikidata", json={"qid": qid}, headers=CSRF)
        ).json()
    assert linked["fetch_pending"] is True and linked["item"] is None

    await _work(fake)

    assert [kind for kind, _ in fake.requests] == ["query", "wbgetentities"]
    async with _client() as api:
        shown = (await api.get(f"/entities/{root_id}/wikidata")).json()
    assert shown["fetch_pending"] is False
    assert shown["identifiers"] == {"viaf": "12345"}
    assert shown["item"]["claims_fetched"] is True
    # The labels came with the fetch, as they would have with the link.
    statuses = {(row["language"], row["text"]): row["status"] for row in shown["names"]}
    assert statuses[("el", f"Φρέσκος {word}")] == "this_entity"


async def test_only_changed_revisions_are_fetched_again() -> None:
    await _no_runs()
    word = _word()
    same, newer = _qid(), _qid()
    same_root = await _linked(f"Same {word}", same)
    newer_root = await _linked(f"Newer {word}", newer)
    fake = FakeWikidata()
    fake.add(
        _person(same, f"Same {word}", revision=100),
        _person(newer, f"Newer {word}", revision=101, aliases={"en": [f"Newest {word}"]}),
    )
    fake.entities[newer]["labels"]["el"] = {"language": "el", "value": f"Νεότερος {word}"}

    async with session_factory() as db, db.begin():
        sweep = await runs.ensure_refresh(db, settings())
    assert sweep is not None
    await _work(fake)

    fetched = [ids.split("|") for ids in fake.actions("wbgetentities")]
    assert any(newer in ids for ids in fetched) and not any(same in ids for ids in fetched)
    assert any(same in titles and newer in titles for titles in fake.actions("query"))
    async with session_factory() as db:
        run = await db.get(WikidataRun, sweep.id)
        assert run is not None and run.status == "finished" and run.changed >= 1
        items = {
            row.qid: row
            for row in await db.scalars(
                select(WikidataItem).where(WikidataItem.qid.in_([same, newer]))
            )
        }
    assert items[newer].revision == 101
    for row in items.values():
        assert row.checked_at > datetime.now(UTC) - timedelta(minutes=5)
    # A refresh never adds names on its own: the new label waits for the user.
    async with _client() as api:
        shown = (await api.get(f"/entities/{newer_root}/wikidata")).json()
    statuses = {(row["language"], row["text"]): row["status"] for row in shown["names"]}
    assert statuses[("el", f"Νεότερος {word}")] == "absent"
    assert statuses[("en", f"Newest {word}")] == "absent"
    assert await _link_value(same_root) == same


async def test_items_checked_lately_wait_unless_all_are_asked_for() -> None:
    await _no_runs()
    qid = _qid()
    await _linked(f"Lately {_word()}", qid, days_ago=1)
    async with session_factory() as db:
        due = await runs.due_refresh_roots(db, settings(), after=None, limit=10_000, force=False)
        everything = await runs.due_refresh_roots(
            db, settings(), after=None, limit=10_000, force=True
        )
    async with session_factory() as db:
        root_id = await db.scalar(
            select(EntityExternalId.entity_id).where(EntityExternalId.value == qid)
        )
    assert root_id not in due and root_id in everything


async def test_a_redirect_moves_the_link_and_says_so() -> None:
    await _no_runs()
    word = _word()
    old, new = _qid(), _qid()
    root_id = await _linked(f"Merged {word}", old)
    fake = FakeWikidata()
    fake.add(_person(new, f"Merged {word}", revision=300))
    fake.redirects[old] = new

    async with session_factory() as db, db.begin():
        await runs.request_refresh(db, root_id)
    await _work(fake)

    assert await _link_value(root_id) == new
    assert _history_actions(await _history(root_id)) == [
        ("wikidata_redirected", {"qid": old}, {"qid": new})
    ]
    async with session_factory() as db:
        run = await db.scalar(select(WikidataRun).where(WikidataRun.entity_id == root_id))
        assert run is not None and (run.status, run.redirected) == ("finished", 1)


def _history_actions(rows: list[tuple[str, object, object]]) -> list[tuple[str, object, object]]:
    return [row for row in rows if row[0] != "wikidata_linked"]


async def test_a_redirect_to_an_item_another_root_holds_waits_for_a_merge() -> None:
    await _no_runs()
    word = _word()
    old, new = _qid(), _qid()
    root_id = await _linked(f"Twin {word}", old)
    holder_id = await _linked(f"Twin Holder {word}", new)
    fake = FakeWikidata()
    fake.add(_person(new, f"Twin {word}"))
    fake.redirects[old] = new

    async with session_factory() as db, db.begin():
        await runs.request_refresh(db, root_id)
    await _work(fake)

    assert await _link_value(root_id) == old
    assert _history_actions(await _history(root_id)) == []
    async with _client() as api:
        shown = (await api.get(f"/entities/{root_id}/wikidata")).json()
    assert shown["item"]["state"] == "redirected" and shown["item"]["redirect_to"] == new
    assert shown["redirect_holder"] == {
        "entity_id": str(holder_id),
        "display_name": f"Twin Holder {word}",
    }


async def test_a_deleted_item_keeps_its_link_and_is_noted_once() -> None:
    await _no_runs()
    qid = _qid()
    root_id = await _linked(f"Gone {_word()}", qid)
    fake = FakeWikidata()
    for _ in range(2):
        async with session_factory() as db, db.begin():
            await runs.request_refresh(db, root_id)
        await _work(fake)

    assert await _link_value(root_id) == qid
    assert _history_actions(await _history(root_id)) == [("wikidata_missing", {"qid": qid}, None)]
    async with session_factory() as db:
        item = await db.get(WikidataItem, qid)
        assert item is not None and item.state == "missing"


async def test_without_a_network_the_refresh_fails_and_changes_nothing() -> None:
    await _no_runs()
    qid = _qid()
    root_id = await _linked(f"Offline {_word()}", qid)
    async with session_factory() as db:
        before = await db.get(WikidataItem, qid)
        assert before is not None
        checked = before.checked_at
    fake = FakeWikidata()

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    fake.failure = offline
    async with session_factory() as db, db.begin():
        run = await runs.request_refresh(db, root_id)
    await _work(fake)

    async with session_factory() as db:
        failed = await db.get(WikidataRun, run.id)
        assert failed is not None and (failed.status, failed.errors) == ("failed", 1)
        after = await db.get(WikidataItem, qid)
        assert after is not None and (after.checked_at, after.revision) == (checked, 100)
    assert await _link_value(root_id) == qid


async def test_the_refresh_button_queues_one_run() -> None:
    await _no_runs()
    root_id = await _linked(f"Button {_word()}", _qid())
    unlinked_id = await _root(f"Unlinked {_word()}")
    async with _client() as api:
        first = await api.post(f"/entities/{root_id}/wikidata/refresh", headers=CSRF)
        again = await api.post(f"/entities/{root_id}/wikidata/refresh", headers=CSRF)
        unlinked = await api.post(f"/entities/{unlinked_id}/wikidata/refresh", headers=CSRF)
        shown = (await api.get(f"/entities/{root_id}/wikidata")).json()
    assert first.status_code == 202, first.text
    assert first.json()["kind"] == "refresh" and again.json()["id"] == first.json()["id"]
    assert unlinked.status_code == 409
    assert shown["fetch_pending"] is True


async def test_the_monthly_refresh_is_queued_only_when_items_are_due() -> None:
    await _no_runs()
    async with session_factory() as db, db.begin():
        await db.execute(
            update(WikidataItem).values(checked_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        assert await runs.ensure_refresh(db, settings()) is None
    await _linked(f"Due {_word()}", _qid(), days_ago=31)
    async with session_factory() as db, db.begin():
        first = await runs.ensure_refresh(db, settings())
        second = await runs.ensure_refresh(db, settings())
    assert first is not None and first.kind == "refresh" and second is None
    # A refresh goes before the candidate sweep.
    async with session_factory() as db, db.begin():
        sweep = await runs.ensure_sweep(db, settings())
    assert sweep is not None
    async with session_factory() as db, db.begin():
        claimed = await runs.claim_run(db, settings())
    assert claimed is not None and claimed[0] == first.id
    await _no_runs()


async def test_the_cli_refreshes_a_qid_and_reports_the_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.cli import print_wikidata_status, run_wikidata_refresh

    await _no_runs()
    qid = _qid()
    root_id = await _linked(f"Cli {_word()}", qid)
    fake = FakeWikidata()
    fake.add(_person(qid, f"Cli {_word()}", revision=200))
    options = settings()
    async with client(fake, options) as wikidata:
        await run_wikidata_refresh(qids=[qid], everything=False, client=wikidata, settings=options)
    out = capsys.readouterr().out
    assert "finished" in out and "changed=1" in out
    async with session_factory() as db:
        item = await db.get(WikidataItem, qid)
        assert item is not None and item.revision == 200
    assert await _link_value(root_id) == qid

    with pytest.raises(SystemExit, match="not linked"):
        async with client(fake, options) as wikidata:
            await run_wikidata_refresh(
                qids=[_qid()], everything=False, client=wikidata, settings=options
            )

    await print_wikidata_status(settings())
    status = capsys.readouterr().out
    assert "wikidata: on" in status and "requests today" in status


async def test_operations_shows_the_throttle_the_load_and_the_last_refresh() -> None:
    await _no_runs()
    until = datetime.now(UTC) + timedelta(minutes=12)
    async with session_factory() as db, db.begin():
        db.add(
            WikidataThrottle(
                id=1,
                paused_until=until,
                pause_reason="rate_limited",
                day=datetime.now(UTC).date(),
                requests_today=42,
            )
        )
        db.add(
            WikidataRun(
                kind="refresh",
                status="finished",
                checked=12,
                changed=3,
                finished_at=datetime.now(UTC),
            )
        )
    async with _client() as api:
        response = await api.get("/operations/wikidata")
    assert response.status_code == 200, response.text
    body = response.json()
    # The test settings set no contact, so Wikidata is off and says why.
    assert body["enabled"] is False and "CONTACT" in body["reason"]
    assert body["throttle"]["state"] == "paused"
    assert body["throttle"]["pause_reason"] == "rate_limited"
    assert body["throttle"]["requests_today"] == 42
    assert body["throttle"]["daily_budget"] == 2000
    assert body["last_refresh"]["checked"] == 12 and body["last_refresh"]["changed"] == 3
    assert isinstance(body["counts"], list) and isinstance(body["runs"], list)
    assert body["links"] >= 0 and body["open_candidates"] >= 0
    await _no_runs()


async def test_migration_0023_adds_the_run_options_and_goes_back() -> None:
    async with session_factory() as db:
        columns = set(
            await db.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'wikidata_runs'"
                )
            )
        )
    assert {"force", "add_labels"} <= columns
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0022")
    try:
        async with session_factory() as db:
            columns = set(
                await db.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'wikidata_runs'"
                    )
                )
            )
        assert not {"force", "add_labels"} & columns
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
