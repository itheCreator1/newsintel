"""What the search index carries for a root with the user's own type, or hidden as not a name."""

import os
import uuid

import pytest
from sqlalchemy import text
from test_entity_authority_elasticsearch import index_name  # noqa: F401 (fixture)
from test_entity_authority_postgres import _article, _entity, _finish
from test_monitor_evaluation_elasticsearch import _index

from app.core.config import get_settings
from app.db.session import session_factory
from app.entities.authority import set_hidden, set_type
from app.search.criteria import build_query, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _entities(index: str, article_id: uuid.UUID) -> dict[str, str]:
    """The indexed entities of one article, id to type."""
    adapter = ElasticsearchAdapter(get_settings().elasticsearch_url)
    response = await adapter.search_index(
        index, {"size": 1, "query": {"term": {"article_id": str(article_id)}}}
    )
    (hit,) = response["hits"]["hits"]
    return {item["id"]: item["type"] for item in hit["_source"]["entities"]}


async def _by_type(index: str, entity_type: str, token: str) -> set[str]:
    async with session_factory() as db:
        criteria = await search_criteria(db, q=token, entity_type=[entity_type])
    response = await ElasticsearchAdapter(get_settings().elasticsearch_url).search_index(
        index, {"size": 20, "query": build_query(criteria, 3), "_source": ["article_id"]}
    )
    return {hit["_source"]["article_id"] for hit in response["hits"]["hits"]}


async def test_a_retyped_root_is_indexed_and_found_under_its_new_type(index_name: str) -> None:  # noqa: F811
    token = uuid.uuid4().hex
    async with session_factory() as db, db.begin():
        topic = await _entity(db, "Artificial Intelligence", "ORG")
        article_id = await _article(db, [(topic, [0])])
        await db.execute(
            text("UPDATE articles SET title = :title WHERE id = :id"),
            {"title": f"Machines {token}", "id": article_id},
        )
        topic_id = topic.id
    await _index(index_name, [article_id])
    assert await _entities(index_name, article_id) == {str(topic_id): "ORG"}

    async with session_factory() as db, db.begin():
        run = await set_type(db, topic_id, "TOPIC")
        assert run is not None
        run_id = run.id
    await _finish(run_id)
    await _index(index_name, [article_id])

    assert await _entities(index_name, article_id) == {str(topic_id): "TOPIC"}
    assert await _by_type(index_name, "TOPIC", token) == {str(article_id)}
    assert await _by_type(index_name, "ORG", token) == set()


async def test_a_hidden_root_leaves_the_index_and_comes_back(index_name: str) -> None:  # noqa: F811
    async with session_factory() as db, db.begin():
        kept = await _entity(db, "Mira Sol", "PERSON")
        noise = await _entity(db, "Last Week", "EVENT")
        article_id = await _article(db, [(kept, [0]), (noise, [9])])
        kept_id, noise_id = kept.id, noise.id

    async with session_factory() as db, db.begin():
        run = await set_hidden(db, noise_id, True)
        assert run is not None
        run_id = run.id
    await _finish(run_id)
    await _index(index_name, [article_id])
    assert await _entities(index_name, article_id) == {str(kept_id): "PERSON"}

    async with session_factory() as db, db.begin():
        run = await set_hidden(db, noise_id, False)
        assert run is not None
        run_id = run.id
    await _finish(run_id)
    await _index(index_name, [article_id])
    assert await _entities(index_name, article_id) == {
        str(kept_id): "PERSON",
        str(noise_id): "EVENT",
    }
