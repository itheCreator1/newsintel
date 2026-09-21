import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from event_fixtures import DIGEST, feed
from sqlalchemy import select
from test_compare_postgres import _article

from app.auth.models import Session, User
from app.clustering.models import ArticleClusterState, ClusterJob
from app.db.session import session_factory
from app.feeds.models import ArticleProcessingJob
from app.nlp.models import ArticleNlpState, NlpJob, NlpProcessorRun
from app.operations import retention
from app.search.models import SearchDelivery, SearchIndexTarget

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]
NOW = datetime.now(UTC)
OLD = NOW - retention.JOB_RETENTION - timedelta(days=1)
OLDER = OLD - timedelta(days=1)
RECENT = NOW - timedelta(days=1)

# Each test adds rows in one transaction, prunes inside it and rolls back. The database is shared
# and pruning is table-wide, so the tests check their own rows by id, never the returned counts.


async def _kept(db, rows) -> set:  # type: ignore[no-untyped-def]
    found = set()
    for row in rows:
        if await db.scalar(select(type(row).id).where(type(row).id == row.id)):
            found.add(row.id)
    return found


def _article_job(article_id, status: str, created: datetime) -> ArticleProcessingJob:  # type: ignore[no-untyped-def]
    return ArticleProcessingJob(
        article_id=article_id, requested_mode="rss", status=status,
        created_at=created, completed_at=created,
    )  # fmt: skip


async def test_article_jobs_keep_the_newest_per_article_and_every_failure() -> None:
    async with session_factory() as db:
        source = await feed(db)
        a, b, c, d = [await _article(db, source, OLD) for _ in range(4)]
        superseded = _article_job(a.id, "succeeded", OLDER)
        newest = _article_job(a.id, "succeeded", OLD)
        only = _article_job(b.id, "succeeded", OLDER)
        failed = _article_job(c.id, "failed", OLDER)
        recent = _article_job(d.id, "succeeded", RECENT - timedelta(hours=1))
        rows = [superseded, newest, only, failed, _article_job(c.id, "succeeded", OLD), recent,
                _article_job(d.id, "succeeded", RECENT)]  # fmt: skip
        db.add_all(rows)
        await db.flush()
        await retention.prune_history(db, NOW)
        kept = await _kept(db, rows)
        await db.rollback()
    assert superseded.id not in kept
    # Automatic processing refuses an article with any job, so its only job must stay.
    assert {newest.id, only.id, failed.id, recent.id} <= kept


def _nlp(state: ArticleNlpState, generation: int, status: str, created: datetime) -> NlpJob:
    return NlpJob(
        state_id=state.id, article_id=state.article_id, processor_name=state.processor_name,
        generation=generation, input_fingerprint=DIGEST, processor_version="t",
        configuration_fingerprint=DIGEST, status=status, created_at=created,
    )  # fmt: skip


async def test_nlp_jobs_keep_the_newest_succeeded_generation_and_its_runs() -> None:
    async with session_factory() as db:
        article = await _article(db, await feed(db), OLD)
        state = ArticleNlpState(
            article_id=article.id, processor_name="entities", input_fingerprint=DIGEST,
            processor_version="t", configuration_fingerprint=DIGEST, requested_generation=5,
        )  # fmt: skip
        db.add(state)
        await db.flush()
        first = _nlp(state, 1, "succeeded", OLDER)
        superseded = _nlp(state, 2, "superseded", OLDER)
        newest = _nlp(state, 3, "succeeded", OLD)
        failed = _nlp(state, 4, "failed", OLD)
        later_superseded = _nlp(state, 5, "superseded", OLD)
        jobs = [first, superseded, newest, failed, later_superseded]
        db.add_all(jobs)
        await db.flush()
        runs = [
            NlpProcessorRun(
                job_id=job.id, article_id=article.id, processor_name="entities",
                processor_version="t", algorithm_version="t", configuration_fingerprint=DIGEST,
                input_fingerprint=DIGEST, generation=job.generation, outcome="success",
            )
            for job in (first, newest)
        ]  # fmt: skip
        db.add_all(runs)
        await db.flush()
        await retention.prune_history(db, NOW)
        kept = await _kept(db, jobs + runs)
        await db.rollback()
    assert not {first.id, superseded.id, runs[0].id} & kept
    # A later failed or superseded generation must not take the provenance run with it.
    assert {newest.id, runs[1].id, failed.id, later_superseded.id} <= kept


async def test_cluster_jobs_keep_the_newest_succeeded_generation() -> None:
    async with session_factory() as db:
        article = await _article(db, await feed(db), OLD)
        state = ArticleClusterState(
            article_id=article.id, algorithm_version="rule-1", requested_generation=3
        )
        db.add(state)
        await db.flush()

        def job(generation: int, status: str) -> ClusterJob:
            return ClusterJob(
                state_id=state.id, article_id=article.id, generation=generation,
                algorithm_version="rule-1", status=status, created_at=OLD,
            )  # fmt: skip

        old, newest, failed = job(1, "succeeded"), job(2, "succeeded"), job(3, "failed")
        db.add_all([old, newest, failed])
        await db.flush()
        await retention.prune_history(db, NOW)
        kept = await _kept(db, [old, newest, failed])
        await db.rollback()
    assert kept == {newest.id, failed.id}


async def test_search_deliveries_are_pruned_only_on_retained_targets() -> None:
    async with session_factory() as db:
        article = await _article(db, await feed(db), OLD)
        targets = {
            role: SearchIndexTarget(
                index_name=f"idx-{uuid.uuid4().hex}", schema_version=1, role=role
            )
            for role in ("retained", "current")
        }
        db.add_all(targets.values())
        await db.flush()

        def delivery(role: str, status: str) -> SearchDelivery:
            return SearchDelivery(
                article_id=article.id, target_id=targets[role].id, requested_revision=1,
                status=status, updated_at=OLD,
            )  # fmt: skip

        stale = delivery("retained", "succeeded")
        rows = [stale, delivery("current", "succeeded")]
        db.add_all(rows)
        await db.flush()
        other = await _article(db, await feed(db), OLD)
        failed = SearchDelivery(
            article_id=other.id, target_id=targets["retained"].id, requested_revision=1,
            status="failed", updated_at=OLD,
        )  # fmt: skip
        db.add(failed)
        await db.flush()
        await retention.prune_history(db, NOW)
        kept = await _kept(db, [*rows, failed])
        await db.rollback()
    assert kept == {rows[1].id, failed.id}


async def test_sessions_expired_or_revoked_long_ago_are_deleted() -> None:
    async with session_factory() as db:
        user = User(username=f"retention-{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(user)
        await db.flush()

        def session(expires: datetime, revoked: datetime | None = None) -> Session:
            return Session(
                token_hash=uuid.uuid4().hex, csrf_token="c", user_id=user.id,
                expires_at=expires, revoked_at=revoked,
            )  # fmt: skip

        expired, revoked = session(OLD), session(NOW + timedelta(hours=1), OLD)
        active, lately = session(NOW + timedelta(hours=1)), session(RECENT)
        rows = [expired, revoked, active, lately]
        db.add_all(rows)
        await db.flush()
        await retention.prune_history(db, NOW)
        kept = await _kept(db, rows)
        await db.rollback()
    assert kept == {active.id, lately.id}


async def test_each_call_deletes_at_most_a_batch_per_table(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(retention, "BATCH", 1)
    async with session_factory() as db:
        source = await feed(db)
        article = await _article(db, source, OLD)
        db.add_all(
            [_article_job(article.id, "succeeded", OLDER - timedelta(days=i)) for i in range(3)]
        )
        db.add(_article_job(article.id, "succeeded", OLD))
        await db.flush()
        deleted = await retention.prune_history(db, NOW)
        await db.rollback()
    assert all(count <= 1 for count in deleted.values())
    assert deleted["article_processing_jobs"] == 1
