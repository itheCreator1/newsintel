import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.search.rebuild import new_index_name


def test_rebuild_indices_are_unique_and_schema_versioned() -> None:
    first = new_index_name(1)
    second = new_index_name(1)

    assert re.fullmatch(r"articles-v1-[0-9]{8}t[0-9]{12}z-[0-9a-f]{8}", first)
    assert first != second


def test_there_is_one_index_layout_and_it_is_the_current_schema() -> None:
    # Older layouts went with Γ1: an older live index is rebuilt, not read with branches.
    from app.search import documents

    assert [name for name in vars(documents) if name.startswith("ARTICLE_INDEX_SETTINGS")] == [
        "ARTICLE_INDEX_SETTINGS"
    ]
    properties = documents.ARTICLE_INDEX_SETTINGS["mappings"]["properties"]
    assert {"entity_text", "keyword_text", "story_cluster_id", "cluster_source_count"} <= set(
        properties
    )
    assert documents.SCHEMA_VERSION >= 3


class _Target:
    def __init__(self, schema_version: int) -> None:
        self.index_name = f"articles-v{schema_version}-test"
        self.schema_version = schema_version


class _Database:
    def __init__(self, target: _Target | None) -> None:
        self.target = target

    async def scalar(self, _query: object) -> _Target | None:
        return self.target


@pytest.mark.asyncio
async def test_an_older_live_index_reports_rebuild_needed() -> None:
    from app.search.criteria import current_search_target
    from app.search.documents import SCHEMA_VERSION

    assert await current_search_target(_Database(_Target(SCHEMA_VERSION))) == (  # type: ignore[arg-type]
        f"articles-v{SCHEMA_VERSION}-test"
    )
    with pytest.raises(HTTPException) as raised:
        await current_search_target(_Database(_Target(SCHEMA_VERSION - 1)))  # type: ignore[arg-type]
    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "search_upgrade_required"  # type: ignore[index]
    assert "Rebuild" in raised.value.detail["message"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_a_delivery_to_an_older_index_is_acknowledged_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Its strict mapping would reject today's document, and nothing reads it any more.
    from app.search import indexing
    from app.search.documents import SCHEMA_VERSION

    delivery = SimpleNamespace(claim_token="token", requested_revision=4)
    acknowledged: list[tuple[object, ...]] = []

    async def load(_delivery_id: uuid.UUID) -> tuple[object, ...]:
        return delivery, "articles-old", SCHEMA_VERSION - 1, object()

    async def acknowledge(*args: object) -> None:
        acknowledged.append(args)

    class _Adapter:
        def __init__(self, _url: str) -> None:
            raise AssertionError("nothing is sent to an older index")

    monkeypatch.setattr(indexing, "_load_document", load)
    monkeypatch.setattr(indexing, "_acknowledge", acknowledge)
    monkeypatch.setattr(indexing, "ElasticsearchAdapter", _Adapter)
    delivery_id = uuid.uuid4()
    await indexing.process_delivery(str(delivery_id), "token")
    assert acknowledged == [(delivery_id, "token", 4, "success")]
