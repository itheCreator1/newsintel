import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from app.clustering.engine import (
    CLUSTER_SCORE_THRESHOLD,
    Assignment,
    load_features,
    score_articles,
)
from app.clustering.execution import process_job
from app.clustering.models import ArticleClusterState, ClusterJob, StoryCluster, StoryClusterMember
from app.clustering.service import claim_job, request_clustering
from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.nlp.execution import process_job as process_nlp_job
from app.nlp.models import ArticleEntity, ArticleNlpState, Entity, NlpJob, NlpProcessorRun
from app.nlp.service import claim_job as claim_nlp_job
from app.nlp.service import request_article_nlp
from app.search.models import ArticleSearchState

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_TIME = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
DIGEST = "f" * 64


async def _feed(db, name: str) -> Feed:  # type: ignore[no-untyped-def]
    feed = Feed(
        name=name,
        url=f"https://example.test/{uuid.uuid4()}.xml",
        tags=[],
        enabled=True,
        poll_interval_minutes=30,
        fetching_mode="rss",
    )
    db.add(feed)
    await db.flush()
    return feed


async def _article(  # type: ignore[no-untyped-def]
    db,
    *,
    feeds: list[Feed],
    title: str,
    title_hash: str,
    hours: float = 0,
) -> Article:
    article = Article(
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title=title,
        normalized_title_hash=title_hash,
        published_at=BASE_TIME + timedelta(hours=hours),
    )
    db.add(article)
    await db.flush()
    for feed in feeds:
        db.add(
            FeedArticle(
                feed_id=feed.id,
                article_id=article.id,
                feed_title=title,
                feed_url=article.original_url,
                description=title,
                metadata_json={},
            )
        )
    await db.flush()
    return article


async def _entity(db, text_value: str) -> Entity:  # type: ignore[no-untyped-def]
    entity = Entity(
        language="en",
        entity_type="ORG",
        normalized_text=f"{text_value}-{uuid.uuid4().hex}",
        display_text=text_value,
    )
    db.add(entity)
    await db.flush()
    return entity


async def _attach_entities(db, article_id, entities) -> None:  # type: ignore[no-untyped-def]
    state = ArticleNlpState(
        article_id=article_id,
        processor_name="entities",
        input_fingerprint=DIGEST,
        processor_version="1",
        configuration_fingerprint=DIGEST,
    )
    db.add(state)
    await db.flush()
    job = NlpJob(
        state_id=state.id,
        article_id=article_id,
        processor_name="entities",
        generation=1,
        input_fingerprint=DIGEST,
        processor_version="1",
        configuration_fingerprint=DIGEST,
    )
    db.add(job)
    await db.flush()
    run = NlpProcessorRun(
        job_id=job.id,
        article_id=article_id,
        processor_name="entities",
        processor_version="1",
        algorithm_version="test-entities",
        configuration_fingerprint=DIGEST,
        input_fingerprint=DIGEST,
        generation=1,
        outcome="success",
    )
    db.add(run)
    await db.flush()
    for entity in entities:
        db.add(
            ArticleEntity(
                article_id=article_id,
                entity_id=entity.id,
                run_id=run.id,
                occurrence_count=1,
                relevance=1.0,
                occurrences=[],
                input_fingerprint=DIGEST,
                is_current=True,
            )
        )
    await db.flush()


async def _run_clustering(article_id: uuid.UUID) -> uuid.UUID:
    """Queue, claim, and run one clustering job the way the scheduler and worker do."""
    async with session_factory() as db, db.begin():
        await request_clustering(db, article_id)
    async with session_factory() as db:
        job_id = await db.scalar(
            select(ClusterJob.id)
            .where(ClusterJob.article_id == article_id, ClusterJob.status == "queued")
            .order_by(ClusterJob.generation.desc())
            .limit(1)
        )
    assert job_id is not None
    async with session_factory() as db:
        claimed = await claim_job(db, job_id, lease_seconds=60)
    assert claimed is not None
    await process_job(job_id, claimed[1])
    return job_id


async def _search_revisions(db, article_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:  # type: ignore[no-untyped-def]
    rows = (
        await db.execute(
            select(ArticleSearchState.article_id, ArticleSearchState.requested_revision).where(
                ArticleSearchState.article_id.in_(article_ids)
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _membership(article_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(StoryClusterMember.article_id, StoryClusterMember.cluster_id).where(
                    StoryClusterMember.article_id.in_(article_ids)
                )
            )
        ).all()
    return {row[0]: row[1] for row in rows}


async def test_two_outlets_covering_one_event_stay_separate_articles_in_one_cluster() -> None:
    title = "Harbor council votes to fund the dredging programme"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Harbor Wire")
        daily = await _feed(db, "Harbor Daily")
        first = await _article(db, feeds=[wire], title=title, title_hash=title_hash)
        second = await _article(db, feeds=[daily], title=title, title_hash=title_hash, hours=3)
        article_ids = [first.id, second.id]

    await _run_clustering(article_ids[0])
    await _run_clustering(article_ids[1])

    membership = await _membership(article_ids)
    assert len(membership) == 2
    assert len(set(membership.values())) == 1
    cluster_id = next(iter(membership.values()))
    async with session_factory() as db:
        cluster = await db.get(StoryCluster, cluster_id)
        articles = await db.scalar(
            select(func.count()).select_from(Article).where(Article.id.in_(article_ids))
        )
        state = await db.scalar(
            select(ArticleClusterState).where(ArticleClusterState.article_id == article_ids[1])
        )
    assert articles == 2, "independently published articles stay separate rows"
    assert cluster is not None
    assert cluster.article_count == 2
    assert cluster.source_count == 2
    assert cluster.representative_article_id == article_ids[0]
    assert cluster.first_published_at == BASE_TIME
    assert cluster.last_published_at == BASE_TIME + timedelta(hours=3)
    assert state is not None and state.completed_generation == state.requested_generation


async def test_one_outlet_publishing_twice_is_not_related_reporting() -> None:
    title = "Coastal authority reopens the northern pier"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Solo Wire")
        first = await _article(db, feeds=[wire], title=title, title_hash=title_hash)
        second = await _article(db, feeds=[wire], title=title, title_hash=title_hash, hours=2)
        article_ids = [first.id, second.id]

    async with session_factory() as db:
        features = await load_features(db, article_ids)
    stop_words = frozenset({"the"})
    # The pair scores well above the threshold, so only the feed-set exclusion can
    # keep it apart.
    assert (
        score_articles(features[article_ids[0]], features[article_ids[1]], stop_words=stop_words)
        > CLUSTER_SCORE_THRESHOLD
    )

    await _run_clustering(article_ids[0])
    await _run_clustering(article_ids[1])

    assert await _membership(article_ids) == {}


async def test_a_bridging_article_merges_the_smaller_cluster_into_the_larger_one() -> None:
    first_hash, second_hash = uuid.uuid4().hex, uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        feeds = [await _feed(db, f"Merge outlet {index}") for index in range(5)]
        port, union_ = await _entity(db, "Port Authority"), await _entity(db, "Dock Union")
        council, ministry = await _entity(db, "City Council"), await _entity(db, "Ministry")
        strike_a = await _article(
            db, feeds=[feeds[0]], title="Harbor strike halts cargo", title_hash=first_hash
        )
        strike_b = await _article(
            db,
            feeds=[feeds[1]],
            title="Harbor strike halts cargo",
            title_hash=first_hash,
            hours=1,
        )
        talks_a = await _article(
            db,
            feeds=[feeds[2]],
            title="Dock strike halts cargo operations",
            title_hash=second_hash,
            hours=2,
        )
        talks_b = await _article(
            db,
            feeds=[feeds[3]],
            title="Dock strike halts cargo operations",
            title_hash=second_hash,
            hours=3,
        )
        bridge = await _article(
            db,
            feeds=[feeds[4]],
            title="Harbor dock strike halts cargo",
            title_hash=uuid.uuid4().hex,
            hours=4,
        )
        for article in (strike_a, strike_b):
            await _attach_entities(db, article.id, [port, union_])
        for article in (talks_a, talks_b):
            await _attach_entities(db, article.id, [council, ministry])
        await _attach_entities(db, bridge.id, [port, union_, council, ministry])
        ids = [strike_a.id, strike_b.id, talks_a.id, talks_b.id, bridge.id]

    for article_id in ids[:4]:
        await _run_clustering(article_id)
    before = await _membership(ids)
    assert len(set(before.values())) == 2, "two separate clusters exist before the merge"
    older_cluster, newer_cluster = before[ids[0]], before[ids[2]]

    assignment_job = await _run_clustering(ids[4])
    after = await _membership(ids)
    assert len(after) == 5
    assert set(after.values()) == {older_cluster}, "ties merge into the older cluster"
    async with session_factory() as db:
        cluster = await db.get(StoryCluster, older_cluster)
        merged_away = await db.get(StoryCluster, newer_cluster)
        job = await db.get(ClusterJob, assignment_job)
    assert merged_away is None, "the merged-away cluster row is deleted"
    assert cluster is not None and cluster.article_count == 5 and cluster.source_count == 5
    assert job is not None and job.status == "succeeded"


async def test_concurrent_workers_converge_on_one_consistent_cluster() -> None:
    title = "Six outlets cover the harbour authority funding decision"
    title_hash = uuid.uuid4().hex
    ids = []
    async with session_factory() as db, db.begin():
        for index in range(6):
            feed = await _feed(db, f"Concurrent outlet {index}")
            article = await _article(
                db, feeds=[feed], title=title, title_hash=title_hash, hours=index
            )
            ids.append(article.id)

    claims = []
    for article_id in ids:
        async with session_factory() as db, db.begin():
            await request_clustering(db, article_id)
        async with session_factory() as db:
            job_id = await db.scalar(
                select(ClusterJob.id).where(ClusterJob.article_id == article_id)
            )
        assert job_id is not None
        async with session_factory() as db:
            claimed = await claim_job(db, job_id, lease_seconds=120)
        assert claimed is not None
        claims.append((job_id, claimed[1]))

    # The worker queue runs these in parallel, and cross-article writes must not collide.
    await asyncio.gather(*(process_job(job_id, token) for job_id, token in claims))

    membership = await _membership(ids)
    assert len(membership) == 6
    assert len(set(membership.values())) == 1
    async with session_factory() as db:
        jobs = list(
            (await db.scalars(select(ClusterJob).where(ClusterJob.article_id.in_(ids)))).all()
        )
        cluster = await db.get(StoryCluster, next(iter(membership.values())))
    assert [job.status for job in jobs] == ["succeeded"] * 6
    assert cluster is not None
    assert cluster.article_count == 6 and cluster.source_count == 6


async def test_rerunning_clustering_is_idempotent() -> None:
    title = "Regional ferry operator publishes the winter timetable"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        first = await _article(
            db, feeds=[await _feed(db, "Ferry Wire")], title=title, title_hash=title_hash
        )
        second = await _article(
            db,
            feeds=[await _feed(db, "Ferry Daily")],
            title=title,
            title_hash=title_hash,
            hours=1,
        )
        ids = [first.id, second.id]

    await _run_clustering(ids[0])
    await _run_clustering(ids[1])
    before = await _membership(ids)
    async with session_factory() as db:
        clusters_before = await db.scalar(select(func.count()).select_from(StoryCluster))
        revisions_before = await _search_revisions(db, ids)

    await _run_clustering(ids[0])
    await _run_clustering(ids[1])
    after = await _membership(ids)
    async with session_factory() as db:
        clusters_after = await db.scalar(select(func.count()).select_from(StoryCluster))
        revisions_after = await _search_revisions(db, ids)
        cluster = await db.get(StoryCluster, after[ids[0]])
        jobs = list(
            (await db.scalars(select(ClusterJob).where(ClusterJob.article_id == ids[0]))).all()
        )
    assert revisions_after == revisions_before, "an unchanged assignment reindexes nothing"
    assert after == before
    assert clusters_after == clusters_before
    assert cluster is not None and cluster.article_count == 2
    assert [job.status for job in sorted(jobs, key=lambda item: item.generation)] == [
        "succeeded",
        "succeeded",
    ]


async def test_clustering_retries_with_backoff_then_fails_terminally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenClusterer:
        version = "rule-1"

        async def assign(self, db, article_id) -> Assignment:  # type: ignore[no-untyped-def]
            raise RuntimeError("clustering backend unavailable")

    monkeypatch.setattr("app.clustering.execution._CLUSTERER", BrokenClusterer())
    async with session_factory() as db, db.begin():
        article = await _article(
            db,
            feeds=[await _feed(db, "Broken Wire")],
            title="A story that cannot be clustered right now",
            title_hash=uuid.uuid4().hex,
        )
        article_id = article.id

    async with session_factory() as db, db.begin():
        await request_clustering(db, article_id)
    async with session_factory() as db:
        job_id = await db.scalar(select(ClusterJob.id).where(ClusterJob.article_id == article_id))
    assert job_id is not None

    statuses = []
    for _ in range(5):
        async with session_factory() as db:
            claimed = await claim_job(db, job_id, lease_seconds=60)
        assert claimed is not None
        await process_job(job_id, claimed[1])
        async with session_factory() as db, db.begin():
            job = await db.get(ClusterJob, job_id, with_for_update=True)
            assert job is not None
            statuses.append(job.status)
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)

    async with session_factory() as db:
        job = await db.get(ClusterJob, job_id)
        state = await db.scalar(
            select(ArticleClusterState).where(ArticleClusterState.article_id == article_id)
        )
        members = await db.scalar(
            select(func.count())
            .select_from(StoryClusterMember)
            .where(StoryClusterMember.article_id == article_id)
        )
    assert statuses == ["retrying", "retrying", "retrying", "retrying", "failed"]
    assert job is not None and job.attempt_count == 5
    assert job.error_category == "clustering"
    assert job.error_message is not None and "unavailable" in job.error_message
    assert state is not None and state.status == "failed"
    assert members == 0


async def test_publishing_entities_chains_a_clustering_request() -> None:
    async with session_factory() as db, db.begin():
        article = await _article(
            db,
            feeds=[await _feed(db, "Chaining Wire")],
            title="English reporting on harbor policy with enough words for the pipeline",
            title_hash=uuid.uuid4().hex,
        )
        await request_article_nlp(db, article.id, processor_names=("entities",))
        article_id = article.id

    async with session_factory() as db:
        nlp_job_id = await db.scalar(select(NlpJob.id).where(NlpJob.article_id == article_id))
    assert nlp_job_id is not None
    async with session_factory() as db:
        claimed = await claim_nlp_job(db, nlp_job_id, lease_seconds=60)
    assert claimed is not None
    await process_nlp_job(nlp_job_id, claimed[1])

    async with session_factory() as db:
        cluster_jobs = list(
            (await db.scalars(select(ClusterJob).where(ClusterJob.article_id == article_id))).all()
        )
    assert len(cluster_jobs) == 1 and cluster_jobs[0].status == "queued"


async def test_downgrade_to_0007_removes_clustering_without_touching_the_archive() -> None:
    async with session_factory() as db:
        articles_before = await db.scalar(select(func.count()).select_from(Article))

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0007")
    try:
        async with session_factory() as db:
            articles_after = await db.scalar(select(func.count()).select_from(Article))
            missing = (
                await db.execute(
                    text(
                        "SELECT to_regclass('story_clusters') IS NULL,"
                        " to_regclass('story_cluster_members') IS NULL,"
                        " to_regclass('article_cluster_state') IS NULL,"
                        " to_regclass('cluster_jobs') IS NULL"
                    )
                )
            ).one()
            indexes = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_indexes"
                    " WHERE indexname = 'ix_articles_effective_date'"
                )
            )
        assert all(missing), "the downgrade removes every Phase 7 table"
        assert indexes == 0
        assert articles_after == articles_before
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
