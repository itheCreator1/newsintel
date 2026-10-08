import os
import uuid

import pytest
from event_fixtures import annotate
from test_clustering_postgres import _article, _entity, _feed
from test_monitor_evaluation_elasticsearch import _index

from app.core.config import get_settings
from app.db.session import session_factory
from app.graph.service import entity_graph
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_graph_picks_the_entity_in_more_articles_over_the_one_with_more_records() -> None:
    """A node slot goes to the entity named in more articles, not to the most nested records.

    Entity B has four records in one article (several current rows, as a merged authority's
    variants will have); entity A has one record in each of three articles.
    """
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    index_name = f"articles-v3-graph-{uuid.uuid4().hex}"
    await adapter.create_index(index_name, ARTICLE_INDEX_SETTINGS_V3)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Graph Wire")
        a = await _entity(db, "Ada Reyes", "PERSON")
        b = await _entity(db, "Bo Lind", "PERSON")
        articles = []
        for entities in ([a], [a], [a], [b, b, b, b]):
            article = await _article(
                db, feeds=[wire], title=f"Graph {uuid.uuid4().hex}", title_hash=uuid.uuid4().hex
            )
            await annotate(db, article.id, entities)
            articles.append(article)
        await db.flush()
    await _index(index_name, [article.id for article in articles])

    async with session_factory() as db:
        graph = await entity_graph(
            db,
            adapter,
            index_name,
            query={"match_all": {}},
            entity_types=["PERSON"],
            nodes=1,
            min_edge_weight=1,
            focus_entity_id=None,
        )

    assert [(node.id, node.article_count) for node in graph.nodes] == [(a.id, 3)]
