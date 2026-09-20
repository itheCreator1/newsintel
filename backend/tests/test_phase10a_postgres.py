import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.clustering.models import StoryCluster, StoryClusterMember
from app.db.session import session_factory
from app.entities.queries import articles, dossier, relationships
from app.feeds.models import Article, Feed, FeedArticle
from app.feeds.service import decode_cursor
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleNlpState,
    Entity,
    NlpJob,
    NlpProcessorRun,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)
DIGEST = "a" * 64


async def _run(db, article_id: uuid.UUID) -> NlpProcessorRun:  # type: ignore[no-untyped-def]
    state = ArticleNlpState(
        article_id=article_id,
        processor_name="entities",
        input_fingerprint=DIGEST,
        processor_version="test",
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
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(job)
    await db.flush()
    run = NlpProcessorRun(
        job_id=job.id,
        article_id=article_id,
        processor_name="entities",
        processor_version="test",
        algorithm_version="test",
        configuration_fingerprint=DIGEST,
        input_fingerprint=DIGEST,
        generation=1,
        outcome="success",
    )
    db.add(run)
    await db.flush()
    return run


async def test_dossier_uses_only_current_annotations_and_keeps_aliases_unavailable() -> None:
    identity_suffix = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        feed = Feed(
            name="Dossier Wire",
            url=f"https://example.test/{uuid.uuid4()}",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Dossier evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=BASE,
            first_discovered_at=BASE - timedelta(hours=2),
        )
        focus = Entity(
            language="en",
            entity_type="ORG",
            normalized_text=f"focus-{identity_suffix}",
            display_text="Focus",
        )
        related = Entity(
            language="en",
            entity_type="PERSON",
            normalized_text=f"related-{identity_suffix}",
            display_text="Related",
        )
        db.add_all([feed, article, focus, related])
        await db.flush()
        db.add(
            FeedArticle(
                feed_id=feed.id,
                article_id=article.id,
                feed_title=article.title,
                feed_url=article.original_url,
                description=None,
                metadata_json={},
            )
        )
        run = await _run(db, article.id)
        db.add_all(
            [
                ArticleEntity(
                    article_id=article.id,
                    entity_id=focus.id,
                    run_id=run.id,
                    occurrence_count=3,
                    relevance=1,
                    occurrences=[{"start": 0, "end": 5}],
                    input_fingerprint=DIGEST,
                    is_current=True,
                ),
                ArticleEntity(
                    article_id=article.id,
                    entity_id=focus.id,
                    run_id=run.id,
                    occurrence_count=99,
                    relevance=1,
                    occurrences=[{"start": 0, "end": 5}],
                    input_fingerprint=DIGEST,
                    is_current=False,
                ),
                ArticleEntity(
                    article_id=article.id,
                    entity_id=related.id,
                    run_id=run.id,
                    occurrence_count=1,
                    relevance=1,
                    occurrences=[],
                    input_fingerprint=DIGEST,
                    is_current=True,
                ),
                ArticleCountryAnnotation(
                    article_id=article.id,
                    run_id=run.id,
                    country_code="GR",
                    role="primary",
                    inferred=False,
                    rule_version="test",
                    occurrence_count=1,
                    occurrences=[],
                    input_fingerprint=DIGEST,
                    is_current=True,
                ),
            ]
        )
        cluster = StoryCluster(
            algorithm_version="test",
            article_count=1,
            source_count=1,
            first_published_at=BASE,
            last_published_at=BASE,
            representative_article_id=article.id,
        )
        db.add(cluster)
        await db.flush()
        db.add(
            StoryClusterMember(
                article_id=article.id, cluster_id=cluster.id, score=1, algorithm_version="test"
            )
        )
        entity_id = focus.id
        article_id = article.id

    async with session_factory() as db:
        focus = await db.get(Entity, entity_id)
        assert focus is not None
        result = await dossier(db, focus, days=30)
        related_result = await relationships(db, entity_id, days=366)
        article_page = await articles(db, entity_id, limit=30, cursor=None)

    assert result.total_mentions == 3
    assert result.article_count == 1
    assert result.cluster_count == 1
    assert result.aliases == []
    assert result.aliases_status == "unavailable"
    assert sum(day.mentions for day in result.timeline) == 3
    assert len(result.timeline) == 30
    assert [item.id for item in article_page.items] == [article_id]
    assert [(item.display_name, item.article_count) for item in related_result.entities] == [
        ("Related", 1)
    ]
    assert [
        (item.country_code, item.role, item.article_count) for item in related_result.countries
    ] == [("GR", "primary", 1)]
    assert [(item.name, item.article_count) for item in related_result.feeds] == [
        ("Dossier Wire", 1)
    ]


async def test_article_pagination_deduplicates_current_annotations_before_limiting() -> None:
    async with session_factory() as db, db.begin():
        feed = Feed(
            name=f"Pagination Wire {uuid.uuid4()}",
            url=f"https://example.test/{uuid.uuid4()}",
            tags=[],
            enabled=True,
            poll_interval_minutes=30,
            fetching_mode="rss",
        )
        focus = Entity(
            language="en",
            entity_type="ORG",
            normalized_text=f"pagination-{uuid.uuid4().hex}",
            display_text="Pagination Focus",
        )
        older = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Older evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=BASE,
            first_discovered_at=BASE,
        )
        newer = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title="Newer evidence",
            normalized_title_hash=uuid.uuid4().hex,
            published_at=BASE + timedelta(hours=1),
            first_discovered_at=BASE + timedelta(hours=1),
        )
        db.add_all([feed, focus, older, newer])
        await db.flush()
        for item in (older, newer):
            db.add(
                FeedArticle(
                    feed_id=feed.id,
                    article_id=item.id,
                    feed_title=item.title,
                    feed_url=item.original_url,
                    description=None,
                    metadata_json={},
                )
            )
        older_run = await _run(db, older.id)
        newer_run = await _run(db, newer.id)
        db.add_all(
            [
                ArticleEntity(
                    article_id=older.id,
                    entity_id=focus.id,
                    run_id=older_run.id,
                    occurrence_count=1,
                    relevance=1,
                    occurrences=[],
                    input_fingerprint=DIGEST,
                    is_current=True,
                ),
                *[
                    ArticleEntity(
                        article_id=newer.id,
                        entity_id=focus.id,
                        run_id=newer_run.id,
                        occurrence_count=1,
                        relevance=1,
                        occurrences=[],
                        input_fingerprint=DIGEST,
                        is_current=True,
                    )
                    for _ in range(2)
                ],
            ]
        )
        entity_id, newer_id, older_id = focus.id, newer.id, older.id

    async with session_factory() as db:
        first_page = await articles(db, entity_id, limit=1, cursor=None)
        assert first_page.next_cursor is not None
        second_page = await articles(
            db, entity_id, limit=1, cursor=decode_cursor(first_page.next_cursor)
        )

    assert [item.id for item in first_page.items] == [newer_id]
    assert [item.id for item in second_page.items] == [older_id]
