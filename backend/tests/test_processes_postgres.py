import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from event_fixtures import DIGEST, feed
from sqlalchemy import delete, text, update
from test_compare_postgres import _article, _get, _statements, _status
from test_operations_postgres import _add_jobs

from app.auth.models import User
from app.clustering.models import ArticleClusterState, ClusterJob
from app.core.config import get_settings
from app.db.session import session_factory
from app.events.models import EventAssociationRun
from app.feeds.models import ArticleProcessingAttempt, ArticleProcessingJob, FeedFetch
from app.monitors.models import Monitor
from app.nlp.models import (
    ArticleNlpState,
    Entity,
    EntityAuthorityRun,
    NlpJob,
    NlpReprocessingRun,
)
from app.operations.models import MaintenanceRun
from app.processes import activity, summary
from app.search.models import SearchDelivery, SearchIndexTarget, SearchRebuild, SourceSearchRefresh
from app.wikidata.models import WikidataRun

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
NOW = datetime.now(UTC)
HOURS = 24
INSIDE = NOW - timedelta(hours=2)
OUTSIDE = NOW - timedelta(hours=30)
LATER = NOW + timedelta(hours=1)  # newer than anything other tests left, so it comes first
SETTINGS = get_settings().model_copy(update={"wikidata_contact": "owner@example.org"})
OFF = get_settings().model_copy(update={"wikidata_enabled": False})

# Like the Operations tests: each test adds rows in one transaction, reads from it and rolls back.
# Where a card shows "the newest run", the test first clears that table inside its transaction.


async def _cards(db, settings=SETTINGS) -> dict[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
    body = await summary.summary(db, NOW, HOURS, settings)
    return {card.key: card.model_dump() for card in body.processes}


def _delta(
    before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]
) -> dict[str, int]:
    return {f: after[f] - before[f] for f in fields}


async def _entity(db, name: str) -> Entity:  # type: ignore[no-untyped-def]
    row = Entity(
        language="en", entity_type="PERSON", normalized_text=uuid.uuid4().hex, display_text=name
    )
    db.add(row)
    await db.flush()
    return row


# -- the summary --------------------------------------------------------------------------------


async def test_the_summary_has_a_card_for_every_process_in_page_order() -> None:
    async with session_factory() as db:
        body = await summary.summary(db, NOW, HOURS, SETTINGS)
        await db.rollback()
    assert [card.key for card in body.processes] == [p.key for p in summary.PROCESSES]
    assert body.window_hours == HOURS and body.window_start == NOW - timedelta(hours=HOURS)
    assert all(card.label and card.description for card in body.processes)


@pytest.mark.parametrize(
    ("kind", "key"),
    [("article", "articles"), ("search", "search"), ("nlp", "nlp"), ("clustering", "clustering")],
)
async def test_a_per_item_card_counts_states_leases_and_the_window(kind: str, key: str) -> None:
    fields = ("queued", "running", "retrying", "failed", "lease_expired",
              "done_in_window", "failed_in_window")  # fmt: skip
    async with session_factory() as db:
        before = (await _cards(db))[key]
        await _add_jobs(db, kind)
        after = (await _cards(db))[key]
        await db.rollback()
    assert _delta(before, after, fields) == {
        "queued": 1, "running": 2, "retrying": 1, "failed": 2, "lease_expired": 1,
        "done_in_window": 1, "failed_in_window": 1,
    }  # fmt: skip
    assert after["oldest_wait_seconds"] >= 400 * 86400
    assert after["state"] == "failing"  # a failure inside the window
    assert after["last_run_at"] is not None


async def test_the_feeds_card_counts_failing_feeds_and_unfinished_fetches() -> None:
    fields = ("queued", "failed", "done_in_window", "failed_in_window")
    async with session_factory() as db:
        before = (await _cards(db))["feeds"]
        failing, healthy, waiting = await feed(db), await feed(db), await feed(db)
        db.add_all(
            [
                FeedFetch(feed_id=failing.id, claim_token=uuid.uuid4().hex, status="success",
                          started_at=OUTSIDE, completed_at=OUTSIDE),
                FeedFetch(feed_id=failing.id, claim_token=uuid.uuid4().hex, status="failed",
                          error_category="http", started_at=INSIDE, completed_at=INSIDE),
                FeedFetch(feed_id=healthy.id, claim_token=uuid.uuid4().hex, status="failed",
                          started_at=OUTSIDE, completed_at=OUTSIDE),
                FeedFetch(feed_id=healthy.id, claim_token=uuid.uuid4().hex, status="success",
                          started_at=INSIDE, completed_at=INSIDE),
                FeedFetch(feed_id=waiting.id, claim_token=uuid.uuid4().hex, status="queued",
                          started_at=LATER),
            ]
        )  # fmt: skip
        await db.flush()
        after = (await _cards(db))["feeds"]
        await db.rollback()
    # One feed's newest finished fetch failed; the other recovered. One fetch waits.
    assert _delta(before, after, fields) == {
        "queued": 1, "failed": 1, "done_in_window": 1, "failed_in_window": 1
    }  # fmt: skip
    assert after["state"] == "failing"
    assert after["last_run_at"] == LATER


async def test_the_monitors_card_counts_due_claimed_and_broken_monitors() -> None:
    fields = ("queued", "running", "failed")
    async with session_factory() as db:
        before = (await _cards(db))["monitors"]
        user = User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(user)
        await db.flush()

        def make(**kw: Any) -> Monitor:
            return Monitor(user_id=user.id, name=uuid.uuid4().hex, kind="search",
                           state_version=1, state={"q": "m"}, **kw)  # fmt: skip

        db.add_all(
            [
                make(next_evaluation_at=NOW - timedelta(hours=1)),
                make(next_evaluation_at=NOW + timedelta(hours=1),
                     claim_token=uuid.uuid4().hex, claim_expires_at=NOW + timedelta(minutes=5)),
                make(next_evaluation_at=NOW + timedelta(hours=1), error_category="search"),
                make(enabled=False, next_evaluation_at=NOW - timedelta(days=1)),
            ]
        )  # fmt: skip
        await db.flush()
        after = (await _cards(db))["monitors"]
        await db.rollback()
    assert _delta(before, after, fields) == {"queued": 1, "running": 1, "failed": 1}
    assert after["state"] == "failing"
    assert after["oldest_wait_seconds"] >= 3600


async def test_an_active_reprocessing_run_shows_its_progress_through_the_selection() -> None:
    async with session_factory() as db:
        await db.execute(delete(NlpReprocessingRun))
        source = await feed(db)
        made = [await _article(db, source, INSIDE) for _ in range(5)]
        run = NlpReprocessingRun(
            status="scanning", processor_names=["entities"], scanned_count=3,
            selection={"article_ids": [str(a.id) for a in made]},
            created_at=LATER,
        )  # fmt: skip
        db.add(run)
        await db.flush()
        card = (await _cards(db))["reprocessing"]
        await db.rollback()
    assert card["state"] == "working"
    assert card["active_run_id"] == run.id
    assert card["progress"] == {"done": 3, "total": 5}
    assert "entities" in card["detail"]


async def test_a_finished_or_failed_reprocessing_run_leaves_the_card_ok_or_failing() -> None:
    async with session_factory() as db:
        await db.execute(delete(NlpReprocessingRun))
        assert (await _cards(db))["reprocessing"]["state"] == "idle"
        done = NlpReprocessingRun(status="succeeded", processor_names=["entities"],
                                  selection={"all": True}, created_at=INSIDE, completed_at=INSIDE)
        db.add(done)
        await db.flush()
        ok = (await _cards(db))["reprocessing"]
        db.add(NlpReprocessingRun(status="failed", processor_names=["keywords"],
                                  selection={"all": True}, created_at=NOW,
                                  error_message="lost the connection"))  # fmt: skip
        await db.flush()
        failing = (await _cards(db))["reprocessing"]
        await db.rollback()
    assert (ok["state"], ok["last_run_at"], ok["active_run_id"]) == ("ok", INSIDE, None)
    assert failing["state"] == "failing"
    assert "lost the connection" in failing["detail"]


async def test_an_authority_run_names_its_kind_and_the_entity() -> None:
    async with session_factory() as db:
        await db.execute(delete(EntityAuthorityRun))
        root = await _entity(db, "Alexis Tsipras")
        run = EntityAuthorityRun(kind="merge", entity_id=root.id, status="running",
                                 created_at=LATER)  # fmt: skip
        db.add(run)
        await db.flush()
        card = (await _cards(db))["authority"]
        await db.rollback()
    assert (card["state"], card["active_run_id"]) == ("working", run.id)
    assert "Alexis Tsipras" in card["detail"] and "erge" in card["detail"]


async def test_an_active_rebuild_shows_articles_scanned_out_of_all() -> None:
    async with session_factory() as db:
        await db.execute(update(SearchRebuild).values(active_key=None))
        target = SearchIndexTarget(index_name=f"idx-{uuid.uuid4().hex}", schema_version=3,
                                   role="replacement")  # fmt: skip
        db.add(target)
        await db.flush()
        rebuild = SearchRebuild(target_id=target.id, status="scanning", scanned_count=7)
        db.add(rebuild)
        await db.flush()
        total = await db.scalar(text("SELECT count(*) FROM articles"))
        card = (await _cards(db))["rebuild"]
        await db.rollback()
    assert (card["state"], card["active_run_id"]) == ("working", rebuild.id)
    assert card["progress"] == {"done": 7, "total": total}


async def test_source_reindexes_count_like_jobs_and_name_the_source() -> None:
    async with session_factory() as db:
        await db.execute(delete(SourceSearchRefresh))
        source = await feed(db)
        db.add_all(
            [
                SourceSearchRefresh(feed_id=source.id, status="running", created_at=LATER),
                SourceSearchRefresh(feed_id=source.id, status="failed", created_at=INSIDE,
                                    error_message="index gone"),
            ]
        )  # fmt: skip
        await db.flush()
        card = (await _cards(db))["source_refresh"]
        await db.rollback()
    assert (card["running"], card["failed"], card["failed_in_window"]) == (1, 1, 1)
    assert card["state"] == "failing"
    assert source.name in card["detail"]


async def test_the_events_card_reports_the_latest_run() -> None:
    async with session_factory() as db:
        await db.execute(delete(EventAssociationRun))
        assert (await _cards(db))["events"]["state"] == "idle"
        db.add(EventAssociationRun(started_at=INSIDE, completed_at=INSIDE, evaluated=4, created=1))
        await db.flush()
        ok = (await _cards(db))["events"]
        db.add(EventAssociationRun(started_at=NOW, completed_at=NOW, error_category="db",
                                   error_message="deadlock"))  # fmt: skip
        await db.flush()
        failing = (await _cards(db))["events"]
        await db.rollback()
    assert (ok["state"], ok["last_run_at"], ok["done_in_window"]) == ("ok", INSIDE, 1)
    assert (failing["state"], failing["failed_in_window"]) == ("failing", 1)
    assert "deadlock" in failing["detail"]


async def test_the_history_cleanup_card_reads_the_maintenance_runs() -> None:
    async with session_factory() as db:
        await db.execute(delete(MaintenanceRun))
        assert (await _cards(db))["retention"]["state"] == "idle"
        db.add(MaintenanceRun(kind="retention", started_at=INSIDE, finished_at=INSIDE,
                              deleted={"nlp_jobs": 1200, "sessions": 30}))  # fmt: skip
        await db.flush()
        card = (await _cards(db))["retention"]
        await db.rollback()
    assert (card["state"], card["last_run_at"]) == ("ok", INSIDE)
    assert "1,230" in card["detail"]


async def test_the_wikidata_cards_are_off_while_wikidata_is_switched_off() -> None:
    async with session_factory() as db:
        cards = await _cards(db, OFF)
        await db.rollback()
    for key in ("wikidata_refresh", "wikidata_candidates"):
        assert cards[key]["state"] == "off"
        assert "switched off" in cards[key]["detail"]


async def test_the_wikidata_cards_follow_their_own_kind_of_run() -> None:
    async with session_factory() as db:
        await db.execute(delete(WikidataRun))
        db.add_all(
            [
                WikidataRun(kind="refresh", status="finished", checked=412, changed=9,
                            created_at=INSIDE, finished_at=INSIDE),
                WikidataRun(kind="candidates", status="failed", error="Could not reach Wikidata",
                            created_at=INSIDE, finished_at=INSIDE),
            ]
        )  # fmt: skip
        await db.flush()
        cards = await _cards(db)
        sweep = WikidataRun(kind="candidates", status="queued", created_at=LATER)
        db.add(sweep)
        await db.flush()
        queued = await _cards(db)
        await db.rollback()
    refresh, candidates = cards["wikidata_refresh"], cards["wikidata_candidates"]
    assert (refresh["state"], refresh["last_run_at"]) == ("ok", INSIDE)
    assert "412 checked" in refresh["detail"] and "9 changed" in refresh["detail"]
    assert candidates["state"] == "failing" and "Could not reach" in candidates["detail"]
    assert queued["wikidata_candidates"]["state"] == "working"
    assert queued["wikidata_candidates"]["active_run_id"] == sweep.id
    assert queued["wikidata_candidates"]["queued"] == 1


async def test_the_summary_route_has_a_fixed_statement_count() -> None:
    first = await _statements("/processes", hours=1)
    assert first == await _statements("/processes", hours=168)
    assert first <= 40


# -- the activity list --------------------------------------------------------------------------


async def _page(db, **kw: Any) -> activity.ActivityPage:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {"process": None, "status": "all", "q": None, "after": None,
                              "limit": 100, "user_id": None}  # fmt: skip
    return await activity.activity(db, NOW, HOURS, **{**values, **kw})


def _mine(page, ids) -> dict[uuid.UUID, Any]:  # type: ignore[no-untyped-def]
    return {item.id: item for item in page.items if item.id in ids}


async def _nlp_job(db, article, status: str, generation: int = 1, **kw: Any) -> NlpJob:  # type: ignore[no-untyped-def]
    state = ArticleNlpState(
        article_id=article.id, processor_name=f"entities-{uuid.uuid4().hex[:6]}",
        input_fingerprint=DIGEST, processor_version="t", configuration_fingerprint=DIGEST,
    )  # fmt: skip
    db.add(state)
    await db.flush()
    job = NlpJob(
        state_id=state.id, article_id=article.id, processor_name="entities", generation=generation,
        input_fingerprint=DIGEST, processor_version="t", configuration_fingerprint=DIGEST,
        status=status, **kw,
    )  # fmt: skip
    db.add(job)
    await db.flush()
    return job


async def test_every_process_lists_its_rows_with_a_readable_title_and_link() -> None:
    async with session_factory() as db:
        source = await feed(db)
        art = await _article(db, source, INSIDE)
        root = await _entity(db, "Ada Lindqvist")
        user = User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(user)
        target = SearchIndexTarget(index_name=f"idx-{uuid.uuid4().hex}", schema_version=3,
                                   role="current")  # fmt: skip
        db.add(target)
        await db.flush()
        cluster_state = ArticleClusterState(article_id=art.id, algorithm_version="rule-1")
        db.add(cluster_state)
        await db.flush()
        rows: dict[str, Any] = {
            "feeds": FeedFetch(feed_id=source.id, claim_token=uuid.uuid4().hex, status="failed",
                               error_category="http", error_message="HTTP 503",
                               started_at=LATER, completed_at=LATER),
            "articles": ArticleProcessingJob(article_id=art.id, requested_mode="rss",
                                             stage="extract", status="failed",
                                             error_category="extract", completed_at=LATER,
                                             created_at=LATER),
            "clustering": ClusterJob(state_id=cluster_state.id, article_id=art.id, generation=1,
                                     algorithm_version="rule-1", status="queued",
                                     created_at=LATER),
            "search": SearchDelivery(article_id=art.id, target_id=target.id,
                                     requested_revision=1, status="retrying",
                                     error_category="elasticsearch", updated_at=LATER),
            "monitors": Monitor(user_id=user.id, name="Greek-Turkish relations", kind="search",
                                state_version=1, state={"q": "x"}, error_category="search",
                                next_evaluation_at=NOW, updated_at=LATER),
            "reprocessing": NlpReprocessingRun(status="scanning", processor_names=["entities"],
                                               selection={"all": True}, created_at=LATER),
            "authority": EntityAuthorityRun(kind="merge", entity_id=root.id, status="running",
                                            created_at=LATER),
            "source_refresh": SourceSearchRefresh(feed_id=source.id, status="succeeded",
                                                  created_at=LATER),
            "events": EventAssociationRun(started_at=LATER, completed_at=LATER, evaluated=3),
            "retention": MaintenanceRun(kind="retention", started_at=LATER, finished_at=LATER,
                                        deleted={"nlp_jobs": 5}),
            "wikidata_refresh": WikidataRun(kind="refresh", status="finished", entity_id=root.id,
                                            created_at=LATER, finished_at=LATER),
            "wikidata_candidates": WikidataRun(kind="candidates", status="stopped",
                                               created_at=LATER, finished_at=LATER),
        }  # fmt: skip
        db.add_all(rows.values())
        await db.flush()
        nlp = await _nlp_job(db, art, "running", created_at=LATER)
        rebuild_target = SearchIndexTarget(index_name=f"idx-{uuid.uuid4().hex}",
                                           schema_version=3, role="replacement")  # fmt: skip
        db.add(rebuild_target)
        await db.flush()
        await db.execute(update(SearchRebuild).values(active_key=None))
        rebuild = SearchRebuild(target_id=rebuild_target.id, status="catching_up",
                                created_at=LATER)  # fmt: skip
        db.add(rebuild)
        await db.flush()
        rows["nlp"], rows["rebuild"] = nlp, rebuild
        page = await _page(db, user_id=user.id)
        await db.rollback()
    found = _mine(page, {row.id for row in rows.values()})
    by_process = {item.process: item for item in found.values()}
    assert set(by_process) == {p.key for p in summary.PROCESSES}
    expected = {
        "feeds": ("failed", source.name, "feed", source.id),
        "articles": ("failed", art.title, "article", art.id),
        "nlp": ("running", art.title, "article", art.id),
        "clustering": ("queued", art.title, "article", art.id),
        "search": ("retrying", art.title, "article", art.id),
        "monitors": ("failed", "Greek-Turkish relations", "monitor", rows["monitors"].id),
        "authority": ("running", "Ada Lindqvist", "entity", root.id),
        "source_refresh": ("succeeded", source.name, "feed", source.id),
        "wikidata_refresh": ("succeeded", "Ada Lindqvist", "entity", root.id),
        "wikidata_candidates": ("stopped", None, None, None),
        "reprocessing": ("running", None, None, None),
        "rebuild": ("running", None, None, None),
        "events": ("succeeded", None, None, None),
        "retention": ("succeeded", None, None, None),
    }
    for key, (status, title, link_kind, link_id) in expected.items():
        item = by_process[key]
        assert item.status == status, key
        if title is not None:
            assert title in item.title, key
        assert item.title, key
        assert (item.link_kind, item.link_id) == (link_kind, link_id), key
    assert by_process["feeds"].error_category == "http"
    assert by_process["feeds"].error_message == "HTTP 503"
    assert by_process["search"].detail and target.index_name in by_process["search"].detail


async def test_monitors_list_only_the_signed_in_users_own() -> None:
    async with session_factory() as db:
        mine, theirs = (User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="x")
                        for _ in range(2))  # fmt: skip
        db.add_all([mine, theirs])
        await db.flush()
        rows = [
            Monitor(user_id=user.id, name=name, kind="search", state_version=1, state={"q": "x"},
                    error_category="search", next_evaluation_at=NOW, updated_at=LATER)
            for user, name in ((mine, "mine"), (theirs, "theirs"))
        ]  # fmt: skip
        db.add_all(rows)
        await db.flush()
        page = await _page(db, user_id=mine.id, process="monitors")
        await db.rollback()
    assert [item.title for item in _mine(page, {r.id for r in rows}).values()] == ["mine"]


async def test_needs_attention_is_running_retrying_and_failures_inside_the_window() -> None:
    async with session_factory() as db:
        source = await feed(db)
        token = uuid.uuid4().hex
        jobs = {}
        plan = (("queued", LATER), ("running", LATER), ("retrying", LATER),
                ("failed", INSIDE), ("failed", OUTSIDE), ("succeeded", INSIDE))  # fmt: skip
        for status, at in plan:
            art = await _article(db, source, INSIDE)
            art.title = f"{token} {status} {at.isoformat()}"
            job = ArticleProcessingJob(article_id=art.id, requested_mode="rss", status=status,
                                       created_at=at, completed_at=at)  # fmt: skip
            db.add(job)
            jobs[(status, at)] = job
        await db.flush()
        pages = {s: await _page(db, status=s, q=token)
                 for s in ("attention", "failed", "running", "queued", "done", "all")}  # fmt: skip
        await db.rollback()

    def seen(name: str) -> set[tuple[str, datetime]]:
        ids = {item.id for item in pages[name].items}
        return {key for key, job in jobs.items() if job.id in ids}

    assert seen("attention") == {("running", LATER), ("retrying", LATER), ("failed", INSIDE)}
    assert seen("failed") == {("failed", INSIDE)}
    assert seen("running") == {("running", LATER), ("retrying", LATER)}
    assert seen("queued") == {("queued", LATER)}
    assert seen("done") == {("succeeded", INSIDE)}
    # The window applies to finished rows in every filter; unfinished ones always show.
    assert seen("all") == set(jobs) - {("failed", OUTSIDE)}
    assert pages["all"].counts.model_dump() == {"attention": 3, "failed": 1, "running": 2,
                                                "queued": 1}  # fmt: skip


async def test_the_list_filters_by_process_and_text_and_skips_superseded_jobs() -> None:
    async with session_factory() as db:
        source = await feed(db)
        token = uuid.uuid4().hex
        art = await _article(db, source, INSIDE)
        art.title = f"Fire at the {token.upper()} warehouse"
        db.add(ArticleProcessingJob(article_id=art.id, requested_mode="rss", status="failed",
                                    created_at=LATER, completed_at=LATER))  # fmt: skip
        nlp = await _nlp_job(db, art, "failed", generation=2, created_at=LATER)
        old = await _nlp_job(db, art, "superseded", created_at=LATER)
        both = await _page(db, q=token)
        only_nlp = await _page(db, q=token, process="nlp")
        await db.rollback()
    assert {item.process for item in both.items} == {"articles", "nlp"}
    assert [item.id for item in only_nlp.items] == [nlp.id]
    assert old.id not in {item.id for item in both.items}


async def test_article_rows_carry_their_attempts() -> None:
    async with session_factory() as db:
        source = await feed(db)
        art = await _article(db, source, INSIDE)
        job = ArticleProcessingJob(article_id=art.id, requested_mode="rss", status="failed",
                                   stage="extract", created_at=LATER, completed_at=LATER)
        db.add(job)
        await db.flush()
        db.add_all(
            [
                ArticleProcessingAttempt(job_id=job.id, stage="fetch", attempt_number=1,
                                         status="succeeded", started_at=INSIDE),
                ArticleProcessingAttempt(job_id=job.id, stage="extract", attempt_number=2,
                                         status="failed", error_category="extract",
                                         error_message="No readable text", started_at=NOW),
            ]
        )  # fmt: skip
        await db.flush()
        page = await _page(db, process="articles", q=art.title)
        await db.rollback()
    [item] = page.items
    assert item.attempt_count == 2
    assert [(a.number, a.stage, a.status, a.error_message) for a in item.attempts] == [
        (1, "fetch", "succeeded", None), (2, "extract", "failed", "No readable text"),
    ]  # fmt: skip


async def test_the_cursor_walks_every_row_once_newest_first() -> None:
    async with session_factory() as db:
        source = await feed(db)
        token = uuid.uuid4().hex
        made = []
        for minute in range(5):
            art = await _article(db, source, INSIDE)
            art.title = f"{token} {minute}"
            at = LATER + timedelta(minutes=minute % 3)  # ties on time break on id
            job = ArticleProcessingJob(article_id=art.id, requested_mode="rss", status="failed",
                                       created_at=at, completed_at=at)  # fmt: skip
            db.add(job)
            made.append(job)
        await db.flush()
        walked: list[Any] = []
        after = None
        for _ in range(10):
            page = await _page(db, q=token, limit=2, after=after)
            walked += page.items
            if page.next_cursor is None:
                break
            after = activity.decode(page.next_cursor)
        await db.rollback()
    assert sorted(i.id for i in walked) == sorted(j.id for j in made)
    keys = [(i.at, i.id) for i in walked]
    assert keys == sorted(keys, reverse=True)


async def test_the_activity_route_answers_and_costs_the_same_however_many_rows() -> None:
    body = await _get("/processes/activity", status="all", limit=5)
    assert set(body) >= {"items", "next_cursor", "counts", "generated_at", "window_hours"}
    small = await _statements("/processes/activity", status="all", limit=1)
    assert small == await _statements("/processes/activity", status="all", limit=100)


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/processes", {"hours": 0}),
        ("/processes", {"hours": 169}),
        ("/processes/activity", {"status": "nope"}),
        ("/processes/activity", {"process": "nope"}),
        ("/processes/activity", {"limit": 101}),
        ("/processes/activity", {"q": "x" * 201}),
    ],
)
async def test_bad_windows_and_filters_are_rejected(path: str, params: dict[str, Any]) -> None:
    assert await _status(path, **params) == 422


@pytest.mark.parametrize("path", ["/processes", "/processes/activity"])
async def test_the_read_routes_need_a_session(path: str) -> None:
    assert await _status(path, signed_in=False) == 401
    assert await _status(path) == 200


# -- migration 0024 -----------------------------------------------------------------------------


async def test_a_stopped_wikidata_run_is_allowed_and_the_downgrade_keeps_it_as_failed() -> None:
    config = Config("alembic.ini")
    async with session_factory() as db, db.begin():
        run = WikidataRun(kind="candidates", status="stopped", finished_at=NOW)
        db.add(run)
    await asyncio.to_thread(command.downgrade, config, "0023")
    try:
        async with session_factory() as db:
            tables = set(await db.scalars(text("SELECT tablename FROM pg_tables")))
            status, error = (
                await db.execute(
                    text("SELECT status, error FROM wikidata_runs WHERE id = :id"), {"id": run.id}
                )
            ).one()
        assert "maintenance_runs" not in tables
        assert (status, error) == ("failed", "Stopped by the user")
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
        async with session_factory() as db, db.begin():
            await db.execute(delete(WikidataRun).where(WikidataRun.id == run.id))
    async with session_factory() as db:
        tables = set(await db.scalars(text("SELECT tablename FROM pg_tables")))
    assert "maintenance_runs" in tables
