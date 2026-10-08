import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from event_fixtures import annotate
from test_clustering_postgres import _article, _entity, _feed
from test_entity_authority_postgres import _finish
from test_monitor_evaluation_elasticsearch import SETTLE, _evaluate, _index, _watch

from app.core.config import get_settings
from app.db.session import session_factory
from app.entities.authority import merge
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
    name = f"articles-v3-authority-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS_V3
    )

    async def target(db: object, criteria: object) -> tuple[str, int]:
        return name, 3

    monkeypatch.setattr(evaluation, "current_search_target", target)
    return name


async def _found(index_name: str, *entity_ids: uuid.UUID) -> set[str]:
    """The article ids a saved investigation filtering on these entities finds now."""
    state = InvestigationState.model_validate({"entity_id": [str(value) for value in entity_ids]})
    async with session_factory() as db:
        criteria = await search_criteria(db, **criteria_params(state))
    response = await ElasticsearchAdapter(get_settings().elasticsearch_url).search_index(
        index_name,
        {"size": 50, "query": build_query(criteria, 3), "_source": ["article_id"]},
    )
    return {hit["_source"]["article_id"] for hit in response["hits"]["hits"]}


async def _two_names(
    index_name: str, discovered: datetime | None = None
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A root and a variant; one indexed article names the variant only."""
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Authority Wire")
        root = await _entity(db, "Quill Harbour Board", "ORG")
        variant = await _entity(db, "QHB", "ORG")
        article = await _article(
            db,
            feeds=[wire],
            title=f"Authority {uuid.uuid4().hex}",
            title_hash=uuid.uuid4().hex,
            discovered=discovered,
        )
        await annotate(db, article.id, [variant])
        ids = root.id, variant.id, str(article.id)
    await _index(index_name, [uuid.UUID(ids[2])])
    return ids


async def test_search_by_root_finds_articles_still_indexed_under_a_variant(
    index_name: str,
) -> None:
    root_id, variant_id, article_id = await _two_names(index_name)
    assert await _found(index_name, root_id) == set()

    # Merged, but the run has not moved the link nor reindexed the article yet.
    async with session_factory() as db, db.begin():
        await merge(db, variant_id=variant_id, target_id=root_id)

    assert await _found(index_name, root_id) == {article_id}
    assert await _found(index_name, variant_id) == {article_id}


async def test_saved_search_with_an_old_id_keeps_working(index_name: str) -> None:
    root_id, variant_id, article_id = await _two_names(index_name)
    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        run_id = run.id
    await _finish(run_id)
    await _index(index_name, [uuid.UUID(article_id)])
    async with session_factory() as db:
        entity = await db.get(Entity, variant_id)
        assert entity is not None and entity.authority_id == root_id

    # The article is indexed under the root now; the variant's id still finds it.
    assert await _found(index_name, variant_id) == {article_id}
    assert await _found(index_name, root_id) == {article_id}


async def test_monitor_with_an_old_id_keeps_matching(index_name: str) -> None:
    now0 = datetime.now(UTC).replace(microsecond=0)
    root_id, variant_id, first = await _two_names(index_name, now0 - timedelta(hours=2))
    watching = await _watch("Old name", "entity", entity_id=[str(variant_id)])
    baseline = await _evaluate(watching, now0)
    assert str(baseline.latest_match_article_id) == first

    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        run_id = run.id
    await _finish(run_id)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Authority Daily")
        root = await db.get(Entity, root_id)
        fresh = await _article(
            db,
            feeds=[wire],
            title=f"Authority {uuid.uuid4().hex}",
            title_hash=uuid.uuid4().hex,
            discovered=now0 + timedelta(minutes=5),
        )
        await annotate(db, fresh.id, [root])
    await _index(index_name, [uuid.UUID(first), fresh.id])

    later = await _evaluate(watching, now0 + timedelta(hours=1) + SETTLE)
    assert later.unseen_article_count == 1
    assert later.latest_match_article_id == fresh.id
