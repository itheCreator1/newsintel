import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from event_fixtures import annotate
from test_clustering_postgres import _article, _entity, _feed
from test_monitor_evaluation_elasticsearch import SETTLE, _evaluate, _index, _watch

from app.core.config import get_settings
from app.db.session import session_factory
from app.entities.relations import add_relation
from app.investigations.schemas import InvestigationState
from app.monitors import evaluation
from app.monitors.evaluation import criteria_params
from app.nlp.models import Entity
from app.search.criteria import build_query, search_criteria
from app.search.documents import ARTICLE_INDEX_SETTINGS_V3
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def index_name(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"articles-v3-relations-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(evaluation, "current_search_target", target)
    return name


async def _found(index_name: str, entity_id: uuid.UUID, *expand: str) -> set[str]:
    state = InvestigationState.model_validate(
        {"entity_id": [str(entity_id)], "entity_expand": list(expand)}
    )
    async with session_factory() as db:
        criteria = await search_criteria(db, **criteria_params(state))
    response = await ElasticsearchAdapter(get_settings().elasticsearch_url).search_index(
        index_name,
        {"size": 50, "query": build_query(criteria, 3), "_source": ["article_id"]},
    )
    return {hit["_source"]["article_id"] for hit in response["hits"]["hits"]}


async def _renamed(
    index_name: str, discovered: datetime | None = None
) -> tuple[uuid.UUID, uuid.UUID, str, str]:
    """An old and a new company name, linked, each named by one indexed article."""
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Relations Wire")
        old = await _entity(db, "Facebook", "ORG")
        new = await _entity(db, "Meta", "ORG")
        articles = []
        for entity in (old, new):
            article = await _article(
                db,
                feeds=[wire],
                title=f"Relations {uuid.uuid4().hex}",
                title_hash=uuid.uuid4().hex,
                discovered=discovered,
            )
            await annotate(db, article.id, [entity])
            articles.append(article.id)
        await add_relation(db, old.id, label="later_name", target_id=new.id)
        ids = old.id, new.id, str(articles[0]), str(articles[1])
    await _index(index_name, [uuid.UUID(ids[2]), uuid.UUID(ids[3])])
    return ids


async def test_search_with_expand_names_finds_old_name_articles(index_name: str) -> None:
    old, new, old_article, new_article = await _renamed(index_name)

    assert await _found(index_name, new, "names") == {old_article, new_article}
    assert await _found(index_name, old, "names") == {old_article, new_article}


async def test_search_without_expand_is_unchanged(index_name: str) -> None:
    old, new, old_article, new_article = await _renamed(index_name)

    assert await _found(index_name, new) == {new_article}
    assert await _found(index_name, new, "parts") == {new_article}


async def test_monitor_with_expand_matches_old_name(index_name: str) -> None:
    now0 = datetime.now(UTC).replace(microsecond=0)
    old, new, _first_old, _first_new = await _renamed(index_name, now0 - timedelta(hours=2))
    watching = await _watch(
        "Meta and its earlier names", "entity", entity_id=[str(new)], entity_expand=["names"]
    )
    await _evaluate(watching, now0)

    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Relations Daily")
        fresh = await _article(
            db,
            feeds=[wire],
            title=f"Relations {uuid.uuid4().hex}",
            title_hash=uuid.uuid4().hex,
            discovered=now0 + timedelta(minutes=5),
        )
        # Still written under the old name.
        await annotate(db, fresh.id, [await db.get(Entity, old)])
    await _index(index_name, [fresh.id])

    later = await _evaluate(watching, now0 + timedelta(hours=1) + SETTLE)
    assert later.unseen_article_count == 1
    assert later.latest_match_article_id == fresh.id
