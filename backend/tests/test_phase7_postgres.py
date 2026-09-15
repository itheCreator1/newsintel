import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.clustering.engine import (
    CLUSTER_SCORE_THRESHOLD,
    Assignment,
    load_features,
    score_articles,
)
from app.clustering.execution import process_job
from app.clustering.models import ArticleClusterState, ClusterJob, StoryCluster, StoryClusterMember
from app.clustering.routes import get_cluster
from app.clustering.service import (
    claim_job,
    count_recluster_selection,
    request_clustering,
    run_recluster,
)
from app.core.config import get_settings
from app.db.session import session_factory
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.routes import get_article
from app.graph.schemas import GraphResponse
from app.graph.service import MAX_NODES, edges_body, focus_query, parse_edges
from app.graph.service import entity_graph as collect_entity_graph
from app.nlp.execution import process_job as process_nlp_job
from app.nlp.models import ArticleEntity, ArticleNlpState, Entity, NlpJob, NlpProcessorRun
from app.nlp.service import claim_job as claim_nlp_job
from app.nlp.service import request_article_nlp
from app.search.criteria import SearchCriteria, build_query
from app.search.documents import ARTICLE_INDEX_SETTINGS_V2, ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter
from app.search.indexing import process_delivery
from app.search.models import ArticleSearchState, SearchDelivery, SearchIndexTarget
from app.search.query import parse_query
from app.search.service import claim_delivery, request_indexing

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
    discovered: datetime | None = None,
) -> Article:
    article = Article(
        original_url=f"https://example.test/{uuid.uuid4()}",
        normalized_url=f"https://example.test/{uuid.uuid4()}",
        title=title,
        normalized_title_hash=title_hash,
        published_at=BASE_TIME + timedelta(hours=hours),
        first_discovered_at=discovered or BASE_TIME + timedelta(hours=hours),
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


async def _entity(db, text_value: str, entity_type: str = "ORG") -> Entity:  # type: ignore[no-untyped-def]
    entity = Entity(
        language="en",
        entity_type=entity_type,
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


def _criteria(**overrides: object) -> SearchCriteria:
    values: dict[str, object] = {
        "parsed": parse_query(""),
        "q": "",
        "sources": [],
        "countries": [],
        "start": None,
        "end": None,
        "content_available": None,
        "processing": [],
        "languages": [],
        "entity_ids": [],
        "entity_types": [],
        "keyword_ids": [],
        "story_countries": [],
        "mentioned_countries": [],
        "story_cluster_ids": [],
    }
    values.update(overrides)
    return SearchCriteria(**values)  # type: ignore[arg-type]


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


async def test_an_article_that_no_longer_matches_dissolves_the_remaining_singleton() -> None:
    title = "Island ferry contract awarded after the review"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        kept = await _article(
            db, feeds=[await _feed(db, "Island Wire")], title=title, title_hash=title_hash
        )
        leaving = await _article(
            db,
            feeds=[await _feed(db, "Island Daily")],
            title=title,
            title_hash=title_hash,
            hours=2,
        )
        ids = [kept.id, leaving.id]

    await _run_clustering(ids[0])
    await _run_clustering(ids[1])
    membership = await _membership(ids)
    assert len(set(membership.values())) == 1
    cluster_id = membership[ids[0]]
    async with session_factory() as db:
        revisions_before = await _search_revisions(db, ids)

    # The departing article is re-titled, so nothing links it to the article it left behind.
    async with session_factory() as db, db.begin():
        article = await db.get(Article, ids[1], with_for_update=True)
        assert article is not None
        article.title = "Unrelated coverage of a regional airport terminal upgrade"
        article.normalized_title_hash = uuid.uuid4().hex

    await _run_clustering(ids[1])

    async with session_factory() as db:
        cluster = await db.get(StoryCluster, cluster_id)
        members = list(
            (
                await db.scalars(
                    select(StoryClusterMember).where(StoryClusterMember.article_id.in_(ids))
                )
            ).all()
        )
        revisions_after = await _search_revisions(db, ids)
        articles = await db.scalar(
            select(func.count()).select_from(Article).where(Article.id.in_(ids))
        )
    assert cluster is None, "a cluster that drops below two members is deleted"
    assert members == [], "the orphaned article keeps no membership row"
    assert revisions_after[ids[0]] > revisions_before[ids[0]], "the orphan is reindexed"
    assert articles == 2, "dissolving a cluster never touches the article archive"


async def test_recluster_selection_counts_then_queues_one_job_for_each_article() -> None:
    window_start = datetime(2026, 7, 1, tzinfo=UTC)
    async with session_factory() as db, db.begin():
        feed = await _feed(db, "Selection Wire")
        inside = [
            await _article(
                db,
                feeds=[feed],
                title=f"Selection article {index}",
                title_hash=uuid.uuid4().hex,
                discovered=window_start + timedelta(days=index),
            )
            for index in range(3)
        ]
        outside = await _article(
            db,
            feeds=[feed],
            title="Selection article outside the window",
            title_hash=uuid.uuid4().hex,
            discovered=window_start + timedelta(days=40),
        )
        ids = [article.id for article in inside]
        all_ids = [*ids, outside.id]

    selection: dict[str, object] = {"article_ids": [str(value) for value in ids]}
    assert await count_recluster_selection(selection) == 3
    async with session_factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ClusterJob)
                .where(ClusterJob.article_id.in_(all_ids))
            )
            == 0
        ), "counting a selection queues nothing"

    # A batch size below the selection size forces the keyset cursor to take a second page.
    assert await run_recluster(selection, batch_size=2) == 3

    async with session_factory() as db:
        jobs = list(
            (
                await db.scalars(select(ClusterJob).where(ClusterJob.article_id.in_(all_ids)))
            ).all()
        )
    assert {job.article_id for job in jobs} == set(ids)
    assert [job.status for job in jobs] == ["queued"] * 3
    assert [job.generation for job in jobs] == [1] * 3

    window = {
        "from_date": window_start.isoformat(),
        "to_date": (window_start + timedelta(days=30)).isoformat(),
    }
    assert await count_recluster_selection(window) >= 3
    async with session_factory() as db:
        excluded = await db.scalar(
            select(func.count())
            .select_from(ClusterJob)
            .where(ClusterJob.article_id == outside.id)
        )
    assert excluded == 0, "an article outside the date range is never selected"

    with pytest.raises(ValueError, match="UTC offset"):
        await count_recluster_selection({"from_date": "2026-07-01"})
    with pytest.raises(ValueError, match="article IDs"):
        await count_recluster_selection({})


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


async def test_article_detail_reports_related_reporting_and_clustering_status() -> None:
    title = "Regional water board approves the reservoir expansion"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Detail Wire")
        daily = await _feed(db, "Detail Daily")
        first = await _article(db, feeds=[wire], title=title, title_hash=title_hash)
        second = await _article(db, feeds=[daily], title=title, title_hash=title_hash, hours=2)
        untouched = await _article(
            db,
            feeds=[await _feed(db, "Untouched Wire")],
            title="A story that was never queued for clustering",
            title_hash=uuid.uuid4().hex,
            hours=4,
        )
        first_id, second_id, untouched_id = first.id, second.id, untouched.id

    await _run_clustering(first_id)
    await _run_clustering(second_id)
    membership = await _membership([first_id, second_id])
    assert len(set(membership.values())) == 1
    cluster_id = membership[first_id]

    async with session_factory() as db:
        detail = await get_article(first_id, db, None)  # type: ignore[arg-type]
    assert detail.clustering_status == "succeeded"
    assert detail.story_cluster is not None
    assert (detail.story_cluster.id, detail.story_cluster.article_count) == (cluster_id, 2)
    assert len(detail.related) == 1
    assert detail.related[0].article_id == second_id
    assert detail.related[0].score > 0

    async with session_factory() as db:
        untouched_detail = await get_article(untouched_id, db, None)  # type: ignore[arg-type]
    assert untouched_detail.clustering_status is None
    assert untouched_detail.story_cluster is None
    assert untouched_detail.related == []


async def test_get_cluster_paginates_members_by_effective_date_then_id() -> None:
    title = "City transit authority unveils the new tram timetable"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        articles = [
            await _article(
                db,
                feeds=[await _feed(db, f"Pagination outlet {index}")],
                title=title,
                title_hash=title_hash,
                hours=index,
            )
            for index in range(3)
        ]
        article_ids = [article.id for article in articles]

    for article_id in article_ids:
        await _run_clustering(article_id)
    membership = await _membership(article_ids)
    assert len(set(membership.values())) == 1
    cluster_id = membership[article_ids[0]]

    async with session_factory() as db:
        page = await get_cluster(cluster_id, db, None, cursor=None, limit=2)  # type: ignore[arg-type]
    assert [item.article_id for item in page.members.items] == list(reversed(article_ids))[:2]
    assert page.members.next_cursor is not None
    assert page.article_count == 3 and page.source_count == 3

    async with session_factory() as db:
        second_page = await get_cluster(  # type: ignore[arg-type]
            cluster_id, db, None, cursor=page.members.next_cursor, limit=2
        )
    assert [item.article_id for item in second_page.members.items] == [article_ids[0]]
    assert second_page.members.next_cursor is None

    async with session_factory() as db:
        with pytest.raises(HTTPException) as excinfo:
            await get_cluster(uuid.uuid4(), db, None, cursor=None, limit=10)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 404


async def test_schema_v3_index_round_trips_cluster_membership_and_a_real_timeline() -> None:
    title = "Two outlets cover the harbour authority recount"
    title_hash = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        first = await _article(
            db, feeds=[await _feed(db, "V3 Wire")], title=title, title_hash=title_hash
        )
        second = await _article(
            db,
            feeds=[await _feed(db, "V3 Daily")],
            title=title,
            title_hash=title_hash,
            hours=1,
        )
        solo = await _article(
            db,
            feeds=[await _feed(db, "V3 Solo")],
            title="An unrelated single-source story about harbour weather",
            title_hash=uuid.uuid4().hex,
            hours=2,
        )
        article_ids = [first.id, second.id, solo.id]

    await _run_clustering(first.id)
    await _run_clustering(second.id)
    membership = await _membership([first.id, second.id])
    assert len(set(membership.values())) == 1
    cluster_id = membership[first.id]

    index_name = f"articles-v3-{uuid.uuid4().hex}"
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    await adapter.create_index(index_name, ARTICLE_INDEX_SETTINGS_V3)

    async with session_factory() as db, db.begin():
        target = SearchIndexTarget(index_name=index_name, schema_version=3, role="replacement")
        db.add(target)
        await db.flush()
        target_id = target.id
        for article_id in article_ids:
            await request_indexing(db, article_id)

    async with session_factory() as db:
        delivery_ids = list(
            (
                await db.scalars(
                    select(SearchDelivery.id).where(SearchDelivery.target_id == target_id)
                )
            ).all()
        )
    assert len(delivery_ids) == 3
    for delivery_id in delivery_ids:
        async with session_factory() as db:
            claimed = await claim_delivery(db, delivery_id, lease_seconds=60)
        assert claimed is not None
        await process_delivery(str(delivery_id), claimed[1])
    await adapter.refresh(index_name)

    async with session_factory() as db:
        deliveries = list(
            (
                await db.scalars(
                    select(SearchDelivery).where(SearchDelivery.target_id == target_id)
                )
            ).all()
        )
    assert [delivery.status for delivery in deliveries] == ["succeeded"] * 3

    clustered_query = build_query(_criteria(story_cluster_ids=[str(cluster_id)]), schema_version=3)
    clustered = await adapter.search_index(index_name, {"query": clustered_query, "size": 10})
    hit_ids = {hit["_source"]["article_id"] for hit in clustered["hits"]["hits"]}
    assert hit_ids == {str(first.id), str(second.id)}
    for hit in clustered["hits"]["hits"]:
        assert hit["_source"]["story_cluster_id"] == str(cluster_id)
        assert hit["_source"]["cluster_source_count"] == 2

    solo_query = build_query(_criteria(), schema_version=3)
    timeline = await adapter.search_index(
        index_name,
        {
            "size": 0,
            "track_total_hits": False,
            "query": solo_query,
            "aggs": {
                "timeline": {
                    "date_histogram": {
                        "field": "effective_date",
                        "calendar_interval": "day",
                        "time_zone": "UTC",
                        "min_doc_count": 0,
                    }
                }
            },
        },
    )
    buckets = timeline["aggregations"]["timeline"]["buckets"]
    assert sum(bucket["doc_count"] for bucket in buckets) == 3

    solo_hit = await adapter.search_index(
        index_name,
        {"query": {"term": {"article_id": str(solo.id)}}, "size": 1},
    )
    solo_source = solo_hit["hits"]["hits"][0]["_source"]
    assert solo_source["story_cluster_id"] is None
    assert solo_source["cluster_source_count"] is None


async def _publish_graph_index(article_ids: list[uuid.UUID]) -> str:
    """Index the given articles into a fresh schema v2 index the graph aggregations can read."""
    index_name = f"articles-v2-graph-{uuid.uuid4().hex}"
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    await adapter.create_index(index_name, ARTICLE_INDEX_SETTINGS_V2)
    async with session_factory() as db, db.begin():
        target = SearchIndexTarget(index_name=index_name, schema_version=2, role="replacement")
        db.add(target)
        await db.flush()
        target_id = target.id
        for article_id in article_ids:
            await request_indexing(db, article_id)
    async with session_factory() as db:
        delivery_ids = list(
            (
                await db.scalars(
                    select(SearchDelivery.id).where(SearchDelivery.target_id == target_id)
                )
            ).all()
        )
    assert len(delivery_ids) == len(article_ids)
    for delivery_id in delivery_ids:
        async with session_factory() as db:
            claimed = await claim_delivery(db, delivery_id, lease_seconds=60)
        assert claimed is not None
        await process_delivery(str(delivery_id), claimed[1])
    await adapter.refresh(index_name)
    return index_name


async def _graph(  # type: ignore[no-untyped-def]
    index_name: str, criteria, focus_entity_id=None, nodes: int = 30, min_edge_weight: int = 2
) -> GraphResponse:
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    async with session_factory() as db:
        return await collect_entity_graph(
            db,
            adapter,
            index_name,
            query=focus_query(build_query(criteria, 2), focus_entity_id),
            entity_types=criteria.entity_types,
            nodes=nodes,
            min_edge_weight=min_edge_weight,
            focus_entity_id=focus_entity_id,
        )


async def test_entity_graph_aggregates_real_co_occurrence_within_a_bounded_payload() -> None:
    async with session_factory() as db, db.begin():
        feed = await _feed(db, "Graph Wire")
        authority = await _entity(db, "Harbour Authority")
        reyes = await _entity(db, "Ada Reyes", entity_type="PERSON")
        trust = await _entity(db, "Port Trust")
        articles = []
        for index, members in enumerate(
            ([authority, reyes, trust], [authority, reyes], [authority, trust])
        ):
            article = await _article(
                db,
                feeds=[feed],
                title=f"Graph coverage {index}",
                title_hash=uuid.uuid4().hex,
                hours=index,
            )
            await _attach_entities(db, article.id, members)
            articles.append(article)
        article_ids = [article.id for article in articles]

    index_name = await _publish_graph_index(article_ids)
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)

    graph = await _graph(index_name, _criteria())

    assert {(node.id, node.text, node.type): node.article_count for node in graph.nodes} == {
        (authority.id, "Harbour Authority", "ORG"): 3,
        (reyes.id, "Ada Reyes", "PERSON"): 2,
        (trust.id, "Port Trust", "ORG"): 2,
    }
    assert graph.nodes[0].id == authority.id
    # Ada Reyes and Port Trust share only one article, which is below the default weight.
    assert {frozenset((edge.source, edge.target)): edge.weight for edge in graph.edges} == {
        frozenset((authority.id, reyes.id)): 2,
        frozenset((authority.id, trust.id)): 2,
    }
    assert graph.truncated is False

    typed = await _graph(index_name, _criteria(entity_types=["PERSON"]))
    assert [(node.id, node.article_count) for node in typed.nodes] == [(reyes.id, 2)]
    assert typed.edges == []

    focused = await _graph(index_name, _criteria(), focus_entity_id=trust.id)
    assert {node.id: node.article_count for node in focused.nodes} == {
        trust.id: 2,
        authority.id: 2,
        reyes.id: 1,
    }
    assert {frozenset((edge.source, edge.target)): edge.weight for edge in focused.edges} == {
        frozenset((trust.id, authority.id)): 2
    }

    # The focus entity survives a type filter that excludes it, and still carries its edges.
    spliced = await _graph(
        index_name, _criteria(entity_types=["PERSON"]), focus_entity_id=trust.id, min_edge_weight=1
    )
    assert {node.id: node.article_count for node in spliced.nodes} == {trust.id: 1, reyes.id: 1}
    assert [frozenset((edge.source, edge.target)) for edge in spliced.edges] == [
        frozenset((trust.id, reyes.id))
    ]

    capped = await _graph(index_name, _criteria(), nodes=1)
    assert [node.id for node in capped.nodes] == [authority.id]
    assert (capped.edges, capped.truncated) == ([], True)

    # Elasticsearch accepts an adjacency matrix at the node cap this API enforces.
    crowd = [str(authority.id), str(reyes.id), str(trust.id)] + [
        str(uuid.uuid4()) for _ in range(MAX_NODES - 3)
    ]
    matrix = await adapter.search_index(index_name, edges_body(build_query(_criteria(), 2), crowd))
    edges, truncated = parse_edges(matrix, node_ids=crowd, min_edge_weight=2)
    assert {frozenset((edge.source, edge.target)) for edge in edges} == {
        frozenset((str(authority.id), str(reyes.id))),
        frozenset((str(authority.id), str(trust.id))),
    }
    assert truncated is False


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
