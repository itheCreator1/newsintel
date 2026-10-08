import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from test_entity_authority_postgres import _article, _entity, _finish, _rows

from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import session_factory
from app.main import create_app
from app.nlp.models import Entity

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

CSRF = {"X-CSRF-Token": "csrf-token"}


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    login = Session(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        csrf_token="csrf-token",
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    app.dependency_overrides[current_session] = lambda: login
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver/api/v1"
    ) as client:
        yield client


async def _pair(root_name: str, variant_name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_factory() as db, db.begin():
        root = await _entity(db, root_name)
        variant = await _entity(db, variant_name)
        return root.id, variant.id


async def _merged(root_name: str, variant_name: str) -> tuple[uuid.UUID, uuid.UUID]:
    root_id, variant_id = await _pair(root_name, variant_name)
    async with session_factory() as db, db.begin():
        (await db.get(Entity, variant_id)).authority_id = root_id  # type: ignore[union-attr]
    return root_id, variant_id


async def test_dossier_redirects_a_merged_id_to_the_root() -> None:
    root_id, variant_id = await _merged("Mara Venn", "M. Venn")
    async with session_factory() as db, db.begin():
        (await db.get(Entity, root_id)).preferred_text = "Venn, Mara"  # type: ignore[union-attr]

    async with _client() as client:
        redirected = (await client.get(f"/entities/{variant_id}")).json()
        direct = (await client.get(f"/entities/{root_id}")).json()

    assert redirected["id"] == str(root_id)
    assert redirected["redirected_from"] == str(variant_id)
    assert redirected["display_name"] == "Venn, Mara"
    assert (redirected["preferred_text"], redirected["status"]) == ("Venn, Mara", "provisional")
    assert (redirected["ambiguous"], redirected["note"]) == (False, None)
    assert direct["id"] == str(root_id) and direct["redirected_from"] is None


async def test_a_variants_articles_are_its_roots() -> None:
    async with session_factory() as db, db.begin():
        root = await _entity(db, "Oren Hale")
        variant = await _entity(db, "O. Hale")
        article_id = await _article(db, [(root, [3])])
        variant.authority_id = root.id
        root_id, variant_id = root.id, variant.id

    async with _client() as client:
        page = (await client.get(f"/entities/{variant_id}/articles")).json()

    assert [item["id"] for item in page["items"]] == [str(article_id)]
    assert root_id != variant_id


async def test_merge_endpoint_starts_a_run_that_moves_the_links() -> None:
    async with session_factory() as db, db.begin():
        root = await _entity(db, "Pia Strand")
        variant = await _entity(db, "P. Strand")
        article_id = await _article(db, [(variant, [7])])
        root_id, variant_id = root.id, variant.id

    async with _client() as client:
        forbidden = await client.post(
            f"/entities/{variant_id}/merge", json={"target_id": str(root_id)}
        )
        response = await client.post(
            f"/entities/{variant_id}/merge", json={"target_id": str(root_id)}, headers=CSRF
        )
        again = await client.post(
            f"/entities/{variant_id}/merge", json={"target_id": str(root_id)}, headers=CSRF
        )
        missing = await client.post(
            f"/entities/{uuid.uuid4()}/merge", json={"target_id": str(root_id)}, headers=CSRF
        )

    assert forbidden.status_code == 403
    assert response.status_code == 202, response.text
    run = response.json()
    assert (run["kind"], run["status"]) == ("merge", "running")
    assert (run["entity_id"], run["root_id"]) == (str(variant_id), str(root_id))
    assert again.status_code == 409
    assert again.json()["detail"] == "This entity is already a variant of that one"
    assert missing.status_code == 404
    await _finish(uuid.UUID(run["id"]))
    assert await _rows(article_id) == [(root_id, variant_id, 1)]


async def test_merge_endpoint_refuses_a_pair_marked_distinct_and_mixed_types() -> None:
    async with session_factory() as db, db.begin():
        person = await _entity(db, "Rhea Cole")
        other = await _entity(db, "Rhea Cole")
        company = await _entity(db, "Cole Group", "ORG")
        ids = person.id, other.id, company.id

    async with _client() as client:
        added = await client.post(f"/entities/{ids[0]}/distinct/{ids[1]}", headers=CSRF)
        distinct = await client.post(
            f"/entities/{ids[1]}/merge", json={"target_id": str(ids[0])}, headers=CSRF
        )
        removed = await client.delete(f"/entities/{ids[1]}/distinct/{ids[0]}", headers=CSRF)
        mixed = await client.post(
            f"/entities/{ids[2]}/merge", json={"target_id": str(ids[0])}, headers=CSRF
        )
        allowed = await client.post(
            f"/entities/{ids[1]}/merge", json={"target_id": str(ids[0])}, headers=CSRF
        )

    assert (added.status_code, removed.status_code) == (204, 204)
    assert distinct.status_code == 409
    assert distinct.json()["detail"] == "These entities are marked as different"
    assert mixed.status_code == 422
    assert allowed.status_code == 202


async def test_split_endpoint_gives_the_mentions_back() -> None:
    async with session_factory() as db, db.begin():
        root = await _entity(db, "Sol Arden")
        variant = await _entity(db, "S. Arden")
        article_id = await _article(db, [(root, [2]), (variant, [30, 50])])
        root_id, variant_id = root.id, variant.id

    async with _client() as client:
        merged = await client.post(
            f"/entities/{variant_id}/merge", json={"target_id": str(root_id)}, headers=CSRF
        )
        await _finish(uuid.UUID(merged.json()["id"]))
        not_a_variant = await client.post(f"/entities/{root_id}/split", headers=CSRF)
        response = await client.post(f"/entities/{variant_id}/split", headers=CSRF)

    assert not_a_variant.status_code == 409
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "split"
    await _finish(uuid.UUID(response.json()["id"]))
    assert await _rows(article_id) == [(variant_id, None, 2), (root_id, None, 1)]


async def test_patch_sets_the_roots_name_status_ambiguity_and_note() -> None:
    root_id, variant_id = await _merged("Tess Morrow", "T. Morrow")

    async with _client() as client:
        forbidden = await client.patch(f"/entities/{root_id}", json={"status": "established"})
        response = await client.patch(
            f"/entities/{root_id}",
            json={
                "preferred_text": "  Morrow,   Tess ",
                "status": "established",
                "ambiguous": True,
                "note": "Port authority spokesperson",
            },
            headers=CSRF,
        )
        untouched = await client.patch(f"/entities/{root_id}", json={}, headers=CSRF)
        cleared = await client.patch(
            f"/entities/{root_id}", json={"preferred_text": None}, headers=CSRF
        )
        variant = await client.patch(
            f"/entities/{variant_id}", json={"preferred_text": "Morrow"}, headers=CSRF
        )
        bad = await client.patch(f"/entities/{root_id}", json={"status": "final"}, headers=CSRF)

    assert forbidden.status_code == 403
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["preferred_text"], body["display_name"]) == ("Morrow, Tess", "Morrow, Tess")
    assert (body["status"], body["ambiguous"], body["note"]) == (
        "established",
        True,
        "Port authority spokesperson",
    )
    # Fields left out stay as they are; an explicit null clears the name.
    assert untouched.json()["preferred_text"] == "Morrow, Tess"
    assert cleared.json()["preferred_text"] is None
    assert cleared.json()["display_name"] == "Tess Morrow"
    assert cleared.json()["status"] == "established"
    assert variant.status_code == 409
    assert bad.status_code == 422


async def test_variants_and_history_of_a_root() -> None:
    root_id, variant_id = await _pair("Uma Pell", "U. Pell")

    async with _client() as client:
        await client.post(
            f"/entities/{variant_id}/merge", json={"target_id": str(root_id)}, headers=CSRF
        )
        await client.patch(f"/entities/{root_id}", json={"status": "established"}, headers=CSRF)
        variants = (await client.get(f"/entities/{root_id}/variants")).json()
        history = (await client.get(f"/entities/{root_id}/history")).json()
        from_variant = (await client.get(f"/entities/{variant_id}/variants")).json()
        missing = await client.get(f"/entities/{uuid.uuid4()}/history")

    assert [(item["id"], item["display_name"]) for item in variants["items"]] == [
        (str(variant_id), "U. Pell")
    ]
    assert from_variant == variants
    assert [(item["action"], item["entity_id"]) for item in history["items"]] == [
        ("merged", str(variant_id)),
        ("status_changed", str(root_id)),
    ]
    merged = history["items"][0]
    assert merged["other_id"] == str(root_id)
    assert merged["after"] == {"authority_id": str(root_id)}
    assert missing.status_code == 404


async def test_picker_finds_a_root_by_a_variants_name_and_lists_only_roots() -> None:
    marker = uuid.uuid4().hex[:10]
    async with session_factory() as db, db.begin():
        root = await _entity(db, f"Vala {marker}")
        root.normalized_text = f"vala {marker}"
        variant = await _entity(db, f"Wren {marker}")
        variant.normalized_text = f"wren {marker}"
        await _article(db, [(root, [1])])
        variant.authority_id = root.id
        root.preferred_text = f"Vala Wren {marker}"
        root_id = root.id

    async with _client() as client:
        by_variant = (await client.get("/nlp/entities", params={"q": f"wren {marker}"})).json()
        by_root = (await client.get("/nlp/entities", params={"q": f"vala {marker}"})).json()

    assert [(item["id"], item["text"]) for item in by_variant["items"]] == [
        (str(root_id), f"Vala Wren {marker}")
    ]
    assert [item["id"] for item in by_root["items"]] == [str(root_id)]


async def test_compare_resolves_a_variant_to_its_root() -> None:
    async with session_factory() as db, db.begin():
        root = await _entity(db, "Xan Ober")
        variant = await _entity(db, "X. Ober")
        other = await _entity(db, "Yara Lune")
        await _article(db, [(root, [1]), (other, [9])])
        variant.authority_id = root.id
        root.preferred_text = "Ober, Xan"
        ids = root.id, variant.id, other.id

    async with _client() as client:
        by_variant = await client.get(
            "/compare", params={"kind": "entity", "a": str(ids[1]), "b": str(ids[2])}
        )
        by_root = await client.get(
            "/compare", params={"kind": "entity", "a": str(ids[0]), "b": str(ids[2])}
        )
        same = await client.get(
            "/compare", params={"kind": "entity", "a": str(ids[1]), "b": str(ids[0])}
        )

    assert by_variant.status_code == 200, by_variant.text
    body = by_variant.json()
    assert (body["a"]["subject"]["ref"], body["a"]["subject"]["label"]) == (
        str(ids[0]),
        "Ober, Xan",
    )
    assert body["overlap"] == by_root.json()["overlap"]
    assert body["overlap"]["articles"]["both"] == 1
    assert same.status_code == 422
    assert same.json()["detail"] == "Choose two different subjects"


async def test_unknown_entities_are_404_on_every_authority_route() -> None:
    unknown = uuid.uuid4()
    async with _client() as client:
        statuses: list[Any] = [
            (await client.get(f"/entities/{unknown}/variants")).status_code,
            (await client.post(f"/entities/{unknown}/split", headers=CSRF)).status_code,
            (await client.patch(f"/entities/{unknown}", json={}, headers=CSRF)).status_code,
            (
                await client.post(f"/entities/{unknown}/distinct/{uuid.uuid4()}", headers=CSRF)
            ).status_code,
        ]
    assert statuses == [404, 404, 404, 404]
