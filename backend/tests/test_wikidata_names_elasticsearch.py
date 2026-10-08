"""A Wikidata label in Greek finds the English articles of the entity it was linked to."""

import os
import random
import uuid

import pytest
import pytest_asyncio
from event_fixtures import annotate
from sqlalchemy import select
from test_clustering_postgres import _article, _entity, _feed
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _finish
from test_monitor_evaluation_elasticsearch import _index

from app.core.config import get_settings
from app.db.session import session_factory
from app.investigations.schemas import InvestigationState
from app.monitors import evaluation
from app.monitors.evaluation import criteria_params
from app.nlp.models import EntityAuthorityRun
from app.search.criteria import build_query, search_criteria
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter
from app.wikidata.cache import store_items
from app.wikidata.parsing import Item

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def index_name(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"articles-v3-wikidata-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(evaluation, "current_search_target", target)
    return name


async def _found(index_name: str, q: str) -> set[str]:
    state = InvestigationState.model_validate({"q": q})
    async with session_factory() as db:
        criteria = await search_criteria(db, **criteria_params(state))
    response = await ElasticsearchAdapter(get_settings().elasticsearch_url).search_index(
        index_name,
        {"size": 50, "query": build_query(criteria, 3), "_source": ["article_id"]},
    )
    return {hit["_source"]["article_id"] for hit in response["hits"]["hits"]}


async def test_the_greek_label_finds_the_english_article(index_name: str) -> None:
    greek = "".join(random.choice("αβγδεζηθικλμνξοπρστυφχψω") for _ in range(10)).capitalize()
    qid = f"Q{random.randrange(10**9, 10**10)}"
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Wikidata Wire")
        root = await _entity(db, "Alexis Example", "PERSON")
        article = await _article(
            db, feeds=[wire], title=f"Talks {uuid.uuid4().hex}", title_hash=uuid.uuid4().hex
        )
        await annotate(db, article.id, [root])
        await store_items(
            db,
            [
                Item(
                    qid=qid,
                    state="ok",
                    revision=1,
                    labels={"en": "Alexis Example", "el": f"Αλέξης {greek}"},
                )
            ],
        )
        root_id, article_id = root.id, article.id
    await _index(index_name, [article_id])
    assert await _found(index_name, greek) == set()

    async with _client() as client:
        linked = await client.post(f"/entities/{root_id}/wikidata", json={"qid": qid}, headers=CSRF)
        assert linked.status_code == 200, linked.text
    async with session_factory() as db:
        run_id = await db.scalar(
            select(EntityAuthorityRun.id).where(
                EntityAuthorityRun.entity_id == root_id, EntityAuthorityRun.kind == "reindex"
            )
        )
    assert run_id is not None
    await _finish(run_id)
    await _index(index_name, [article_id])

    assert await _found(index_name, greek) == {str(article_id)}
