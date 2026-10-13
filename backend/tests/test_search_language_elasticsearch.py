"""Search finds a word whatever its accents, case or ending, in Greek and in English (fix plan Γ2).

The standard analyzer alone left "ΑΘΗΝΑ" and "Αθήνα" as two different words, and "floods" and
"flood" too.
"""

import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from test_clustering_postgres import _article, _feed
from test_monitor_api_postgres import _client, _user
from test_monitor_evaluation_elasticsearch import _index

from app.core.config import get_settings
from app.db.session import session_factory
from app.search import routes
from app.search.documents import ARTICLE_INDEX_SETTINGS
from app.search.elasticsearch import ElasticsearchAdapter

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL and Elasticsearch fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

TITLES = {
    "athens": "ΣΥΝΑΝΤΗΣΗ ΣΤΗΝ ΑΘΗΝΑ ΓΙΑ ΤΟ ΝΕΡΟ",
    "tsipras": "Δήλωση του Τσίπρα για τις εκλογές",
    "government": "Η απόφαση της κυβέρνησης για τα σχολεία",
    "floods": "Floods close the coastal road",
    "power_cut": "A power cut darkens the north",
    "power_cuts": "Power cuts spread across the south",
}


@pytest_asyncio.fixture(loop_scope="session")
async def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """One article per title, alone in a fresh index."""
    name = f"articles-language-{uuid.uuid4().hex}"
    await ElasticsearchAdapter(get_settings().elasticsearch_url).create_index(
        name, ARTICLE_INDEX_SETTINGS
    )

    async def target(db: object) -> str:
        return name

    monkeypatch.setattr(routes, "current_search_target", target)
    async with session_factory() as db, db.begin():
        wire = await _feed(db, "Language Wire")
        ids = {
            key: (await _article(db, feeds=[wire], title=title, title_hash=uuid.uuid4().hex)).id
            for key, title in TITLES.items()
        }
    await _index(name, list(ids.values()))
    return SimpleNamespace(**{key: str(value) for key, value in ids.items()})


async def _search(q: str) -> list[dict[str, Any]]:
    async with _client(await _user()) as client:
        response = await client.get("/api/v1/search", params={"q": q, "limit": 20})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def _found(q: str) -> set[str]:
    return {item["article_id"] for item in await _search(q)}


async def test_a_capital_headline_without_accents_matches_an_accented_query(world: Any) -> None:
    assert await _found("Αθήνα") == {world.athens}
    assert await _found("αθηνα") == {world.athens}


async def test_greek_inflections_match(world: Any) -> None:
    assert await _found("Τσίπρας") == {world.tsipras}
    assert await _found("κυβέρνηση") == {world.government}


async def test_english_plurals_match(world: Any) -> None:
    assert await _found("flood") == {world.floods}
    assert await _found("cut") == {world.power_cut, world.power_cuts}


async def test_a_quoted_phrase_stays_exact(world: Any) -> None:
    assert await _found('"power cut"') == {world.power_cut}
    assert await _found('"power cuts"') == {world.power_cuts}


async def test_a_word_found_in_another_form_is_highlighted(world: Any) -> None:
    (item,) = await _search("Αθήνα")
    assert {segment["text"] for segment in item["highlights"] if segment["marked"]} == {"ΑΘΗΝΑ"}
