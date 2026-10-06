import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.clustering.models import StoryCluster, StoryClusterMember
from app.events.models import Event, EventCluster
from app.feeds.models import Article, Feed, FeedArticle
from app.nlp.models import (
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleNlpState,
    Entity,
    NlpJob,
    NlpProcessorRun,
)

# Relative to now, since `reconcile_events` only looks at recent events.
BASE = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
DIGEST = "a" * 64
FIRE = "harbour warehouse fire"


async def feed(db) -> Feed:  # type: ignore[no-untyped-def]
    item = Feed(name=f"Feed {uuid.uuid4().hex[:6]}", url=f"https://example.test/{uuid.uuid4()}")
    db.add(item)
    await db.flush()
    return item


async def entities(db, count: int) -> list[Entity]:  # type: ignore[no-untyped-def]
    rows = [
        Entity(
            language="en",
            entity_type="ORG",
            normalized_text=uuid.uuid4().hex,
            display_text=f"Entity {i}",
        )
        for i in range(count)
    ]
    db.add_all(rows)
    await db.flush()
    return rows


async def annotate(db, article_id, ents, country=None) -> None:  # type: ignore[no-untyped-def]
    processor = f"entities-{uuid.uuid4().hex[:8]}"  # an article may be annotated more than once
    state = ArticleNlpState(
        article_id=article_id,
        processor_name=processor,
        input_fingerprint=DIGEST,
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(state)
    await db.flush()
    job = NlpJob(
        state_id=state.id,
        article_id=article_id,
        processor_name=processor,
        generation=1,
        input_fingerprint=DIGEST,
        processor_version="test",
        configuration_fingerprint=DIGEST,
    )
    db.add(job)
    await db.flush()
    nlp_run = NlpProcessorRun(
        job_id=job.id,
        article_id=article_id,
        processor_name=processor,
        processor_version="test",
        algorithm_version="test",
        configuration_fingerprint=DIGEST,
        input_fingerprint=DIGEST,
        generation=1,
        outcome="success",
    )
    db.add(nlp_run)
    await db.flush()
    db.add_all(
        ArticleEntity(
            article_id=article_id,
            entity_id=entity.id,
            run_id=nlp_run.id,
            occurrence_count=1,
            relevance=1,
            occurrences=[],
            input_fingerprint=DIGEST,
            is_current=True,
        )
        for entity in ents
    )
    if country:
        db.add(
            ArticleCountryAnnotation(
                article_id=article_id,
                run_id=nlp_run.id,
                country_code=country,
                role="primary",
                inferred=False,
                rule_version="test",
                occurrence_count=1,
                occurrences=[],
                input_fingerprint=DIGEST,
                is_current=True,
            )
        )
    await db.flush()


async def story(  # type: ignore[no-untyped-def]
    db, ents, *, title=FIRE, hours=0.0, country=None, articles=2, feeds=()
) -> StoryCluster:
    """A cluster of `articles` real articles that all mention `ents`, published at BASE+hours.

    Each article is also discovered by every feed in `feeds`, so source counts can be tested.
    """
    at = BASE + timedelta(hours=hours)
    cluster = StoryCluster(
        algorithm_version="rule-1",
        article_count=articles,
        source_count=articles,
        first_published_at=at,
        last_published_at=at,
    )
    db.add(cluster)
    await db.flush()
    for i in range(articles):
        article = Article(
            original_url=f"https://example.test/{uuid.uuid4()}",
            normalized_url=f"https://example.test/{uuid.uuid4()}",
            title=title,
            normalized_title_hash=uuid.uuid4().hex,
            published_at=at,
            first_discovered_at=at,
        )
        db.add(article)
        await db.flush()
        if i == 0:
            cluster.representative_article_id = article.id
        db.add(
            StoryClusterMember(
                article_id=article.id, cluster_id=cluster.id, score=1, algorithm_version="rule-1"
            )
        )
        await annotate(db, article.id, ents, country)
        for source in feeds:
            db.add(
                FeedArticle(
                    feed_id=source.id,
                    article_id=article.id,
                    guid=uuid.uuid4().hex,
                    feed_title=title,
                    feed_url=article.original_url,
                    metadata_json={},
                )
            )
    await db.flush()
    return cluster


async def event_of(db, cluster_id, version) -> Event | None:  # type: ignore[no-untyped-def]
    return await db.scalar(
        select(Event)
        .join(EventCluster, EventCluster.event_id == Event.id)
        .where(EventCluster.cluster_id == cluster_id, EventCluster.algorithm_version == version)
    )


async def analyzed_event_tables(db) -> None:  # type: ignore[no-untyped-def]
    """Fill the event tables and analyze them, for a test that asserts which index a query uses.

    On the near-empty tables the suite leaves, every index costs the same and the one EXPLAIN names
    is settled by leftover statistics: an autoanalyze that happens to catch a table empty flips it.
    With a couple of thousand rows and fresh statistics the choice follows from the query. The
    caller rolls back, which removes the rows again.
    """
    series = "generate_series(1, 2000) AS n"
    event, cluster = "md5('plan-event-' || n)::uuid", "md5('plan-cluster-' || n)::uuid"
    for statement in (
        "INSERT INTO events (id, algorithm_version, status, started_at, ended_at, primary_country)"
        f" SELECT {event}, 'plan-statistics', CASE WHEN n % 10 = 0 THEN 'active' ELSE 'closed' END,"
        " now() - n * interval '1 hour', now() - n * interval '1 hour',"
        f" CASE WHEN n % 20 = 0 THEN 'GR' ELSE 'US' END FROM {series}",
        "INSERT INTO story_clusters (id, algorithm_version)"
        f" SELECT {cluster}, 'plan-statistics' FROM {series}",
        "INSERT INTO event_clusters (event_id, cluster_id, algorithm_version, score)"
        f" SELECT {event}, {cluster}, 'plan-statistics', 1 FROM {series}",
        "INSERT INTO nlp_entities (id, language, entity_type, normalized_text, display_text)"
        " SELECT md5('plan-entity-' || m)::uuid, 'en', 'ORG', 'plan-statistics-' || m, 'Plan'"
        " FROM generate_series(0, 199) AS m",
        "INSERT INTO event_entities (event_id, entity_id, article_count)"
        f" SELECT {event}, md5('plan-entity-' || (n + k) % 200)::uuid, 1"
        f" FROM {series}, generate_series(0, 2) AS k",
        "ANALYZE events",
        "ANALYZE event_clusters",
        "ANALYZE event_entities",
    ):
        await db.execute(text(statement))
