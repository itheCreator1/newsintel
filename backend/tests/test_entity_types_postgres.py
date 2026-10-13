"""The user's own type for a root, and roots hidden as "not a name"."""

import json
import os
import uuid

import pytest
from sqlalchemy import select
from test_authority_suggestions_postgres import _language, _named
from test_entity_authority_api_postgres import CSRF, _client
from test_entity_authority_postgres import _article, _entity, _finish

from app.db.session import session_factory
from app.nlp.models import Entity, EntityAuthorityChange, EntityAuthorityRun

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _changes(entity_id: uuid.UUID) -> list[tuple[str, object, object]]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(EntityAuthorityChange)
            .where(EntityAuthorityChange.entity_id == entity_id)
            .order_by(EntityAuthorityChange.created_at)
        )
        return [(row.action, row.before, row.after) for row in rows]


# -- the type ----------------------------------------------------------------------------------


async def test_a_roots_type_is_the_users_when_set_and_the_models_otherwise() -> None:
    from app.entities.authority import set_type

    async with session_factory() as db, db.begin():
        entity = await _entity(db, "Artificial Intelligence", "ORG")
        assert entity.kind == "ORG"
        run = await set_type(db, entity.id, "TOPIC")
        assert run is not None
        entity_id, run_id = entity.id, run.id

    async with session_factory() as db:
        entity = await db.get(Entity, entity_id)
        assert entity is not None
        # The model's type stays: it is part of the name NER finds the row by.
        assert (entity.entity_type, entity.preferred_type, entity.kind) == ("ORG", "TOPIC", "TOPIC")
        found = await db.scalar(
            select(Entity.id).where(Entity.kind == "TOPIC", Entity.id == entity_id)
        )
        assert found == entity_id
        run = await db.get(EntityAuthorityRun, run_id)
        assert run is not None and run.kind == "reindex"
    assert await _changes(entity_id) == [
        ("type_changed", {"entity_type": "ORG"}, {"entity_type": "TOPIC"})
    ]


async def test_the_models_own_type_clears_the_choice_and_the_same_type_does_nothing() -> None:
    from app.entities.authority import set_type

    async with session_factory() as db, db.begin():
        entity = await _entity(db, "Quiet Harbour", "ORG", preferred_type="LOCATION")
        entity_id = entity.id
    async with session_factory() as db, db.begin():
        assert await set_type(db, entity_id, "LOCATION") is None
        assert await set_type(db, entity_id, "ORG") is not None
    async with session_factory() as db:
        entity = await db.get(Entity, entity_id)
        assert entity is not None and (entity.preferred_type, entity.kind) == (None, "ORG")
    assert [action for action, _, _ in await _changes(entity_id)] == ["type_changed"]


async def test_a_variant_or_an_unknown_type_is_refused() -> None:
    from app.entities.authority import AuthorityError, set_type

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Lena Moro")
        variant = await _entity(db, "L. Moro", authority_id=root.id)
        variant_id, root_id = variant.id, root.id
    async with session_factory() as db, db.begin():
        with pytest.raises(AuthorityError) as refused:
            await set_type(db, variant_id, "ORG")
        assert refused.value.status_code == 409
        with pytest.raises(AuthorityError) as unknown:
            await set_type(db, root_id, "COUNTRY")
        assert unknown.value.status_code == 422


async def test_the_reindex_run_of_a_type_change_reindexes_the_roots_articles() -> None:
    from app.entities.authority import set_type
    from app.search.models import ArticleSearchState

    async with session_factory() as db, db.begin():
        entity = await _entity(db, "A.I.", "PERSON")
        article_id = await _article(db, [(entity, [0])])
        entity_id = entity.id
    async with session_factory() as db:
        state = await db.get(ArticleSearchState, article_id)
        before = state.requested_revision if state else 0
    async with session_factory() as db, db.begin():
        run = await set_type(db, entity_id, "TOPIC")
        assert run is not None
        run_id = run.id
    await _finish(run_id)
    async with session_factory() as db:
        state = await db.get(ArticleSearchState, article_id)
        assert state is not None and state.requested_revision == before + 1


# -- hidden ------------------------------------------------------------------------------------


async def test_hiding_a_root_is_recorded_reindexes_and_can_be_undone() -> None:
    from app.entities.authority import AuthorityError, set_hidden

    async with session_factory() as db, db.begin():
        root = await _entity(db, "Last Week", "EVENT")
        variant = await _entity(db, "last week's", "EVENT", authority_id=root.id)
        root_id, variant_id = root.id, variant.id
    async with session_factory() as db, db.begin():
        assert await set_hidden(db, root_id, True) is not None
        assert await set_hidden(db, root_id, True) is None
        with pytest.raises(AuthorityError):
            await set_hidden(db, variant_id, True)
    async with session_factory() as db, db.begin():
        assert await set_hidden(db, root_id, False) is not None
    assert await _changes(root_id) == [
        ("hidden_changed", {"hidden": False}, {"hidden": True}),
        ("hidden_changed", {"hidden": True}, {"hidden": False}),
    ]


async def test_hidden_roots_are_left_out_of_the_duplicate_suggestions() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "ORG", "european union", hidden=True)
        await _named(db, language, "ORG", "eu")
        await _named(db, language, "PERSON", "donald trump")
        await _named(db, language, "PERSON", "d. trump")
    async with session_factory() as db:
        found = await suggest(db, language=language)
    assert {item.root.normalized_text for item in found} | {
        item.variant.normalized_text for item in found
    } == {"donald trump", "d. trump"}


async def test_clustering_counts_the_users_type_and_never_a_hidden_root() -> None:
    from app.clustering.engine import load_features

    async with session_factory() as db, db.begin():
        kept = await _entity(db, "Mira Sol", "PERSON")
        topic = await _entity(db, "Artificial Intelligence", "ORG", preferred_type="TOPIC")
        promoted = await _entity(db, "Harbour Gala", "PRODUCT", preferred_type="EVENT")
        hidden = await _entity(db, "Tuesday", "ORG", hidden=True)
        article_id = await _article(
            db, [(kept, [0]), (topic, [5]), (promoted, [10]), (hidden, [15])]
        )
        expected = {kept.id, promoted.id}
    async with session_factory() as db:
        features = await load_features(db, [article_id])
    assert features[article_id].entity_ids == expected


# -- the API -----------------------------------------------------------------------------------


async def test_patch_sets_the_type_and_hides_and_the_dossier_shows_both() -> None:
    async with session_factory() as db, db.begin():
        entity = await _entity(db, "Machine Learning", "PERSON")
        entity_id = entity.id

    async with _client() as client:
        changed = await client.patch(
            f"/entities/{entity_id}", json={"entity_type": "TOPIC", "hidden": True}, headers=CSRF
        )
        dossier = (await client.get(f"/entities/{entity_id}")).json()
        unknown = await client.patch(
            f"/entities/{entity_id}", json={"entity_type": "WIDGET"}, headers=CSRF
        )
        reset = await client.patch(
            f"/entities/{entity_id}", json={"entity_type": None, "hidden": False}, headers=CSRF
        )

    assert changed.status_code == 200
    body = changed.json()
    assert (body["entity_type"], body["ner_type"], body["hidden"]) == ("TOPIC", "PERSON", True)
    assert (dossier["entity_type"], dossier["ner_type"], dossier["hidden"]) == (
        "TOPIC",
        "PERSON",
        True,
    )
    assert unknown.status_code == 422
    assert (reset.json()["entity_type"], reset.json()["hidden"]) == ("PERSON", False)


async def test_the_authority_file_shows_the_users_type_and_hidden_roots_only_when_asked() -> None:
    language = _language()
    async with session_factory() as db, db.begin():
        shown = await _named(db, language, "ORG", "deep learning", preferred_type="TOPIC")
        hidden = await _named(db, language, "ORG", "yesterday", hidden=True)
        shown_id, hidden_id = str(shown.id), str(hidden.id)

    async with _client() as client:
        default = (await client.get("/authorities", params={"language": language})).json()
        only_hidden = (
            await client.get("/authorities", params={"language": language, "hidden": "true"})
        ).json()

    assert [(item["id"], item["entity_type"], item["hidden"]) for item in default["items"]] == [
        (shown_id, "TOPIC", False)
    ]
    assert [item["id"] for item in only_hidden["items"]] == [hidden_id]


async def test_the_entity_lookup_shows_the_users_type_and_skips_hidden_roots() -> None:
    token = uuid.uuid4().hex[:10]
    async with session_factory() as db, db.begin():
        shown = Entity(
            language="en",
            entity_type="ORG",
            normalized_text=f"neural {token}",
            display_text=f"Neural {token}",
            preferred_type="TOPIC",
        )
        hidden = Entity(
            language="en",
            entity_type="ORG",
            normalized_text=f"neural {token} weekly",
            display_text=f"Neural {token} weekly",
            hidden=True,
        )
        db.add_all([shown, hidden])
        await db.flush()
        await _article(db, [(shown, [0]), (hidden, [5])])

    async with _client() as client:
        found = (await client.get("/nlp/entities", params={"q": f"neural {token}"})).json()

    assert [(item["text"], item["kind"]) for item in found["items"]] == [
        (f"Neural {token}", "TOPIC")
    ]


# -- export and import -------------------------------------------------------------------------


async def test_the_type_and_hidden_travel_in_the_file() -> None:
    from app.entities.transfer import export_authorities, import_authorities

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "ORG", "artificial intelligence", preferred_type="TOPIC")
        await _named(db, language, "EVENT", "last week", hidden=True)
    async with session_factory() as db:
        exported = await export_authorities(db, language=language)
    assert exported["version"] == 3
    marks = {
        item["normalized_text"]: (item["preferred_type"], item["hidden"])
        for item in exported["entities"]
    }
    assert marks == {"artificial intelligence": ("TOPIC", False), "last week": (None, True)}

    other = _language()
    data = json.loads(json.dumps(exported).replace(f'"{language}"', f'"{other}"'))
    async with session_factory() as db, db.begin():
        report = await import_authorities(db, data)
    assert report.created == 2 and not report.conflicts
    async with session_factory() as db:
        rows = {
            row.normalized_text: (row.kind, row.hidden)
            for row in await db.scalars(select(Entity).where(Entity.language == other))
        }
    assert rows == {"artificial intelligence": ("TOPIC", False), "last week": ("EVENT", True)}

    # A version 2 file, written before types could change, still imports.
    old = {**data, "version": 2}
    for item in old["entities"]:
        item.pop("preferred_type")
        item.pop("hidden")
    async with session_factory() as db, db.begin():
        assert not (await import_authorities(db, old)).conflicts


# -- merges across types (PR-B) ----------------------------------------------------------------


async def test_a_name_of_another_type_merges_and_counts_as_the_roots_type() -> None:
    from app.entities.authority import merge

    async with session_factory() as db, db.begin():
        root = await _entity(db, "AI", "ORG")
        variant = await _entity(db, "A.I.", "PERSON")
        article_id = await _article(db, [(variant, [0, 9])])
        root_id, variant_id = root.id, variant.id
    async with session_factory() as db, db.begin():
        run = await merge(db, variant_id=variant_id, target_id=root_id)
        run_id = run.id
    await _finish(run_id)

    async with session_factory() as db:
        variant = await db.get(Entity, variant_id)
        root = await db.get(Entity, root_id)
        assert variant is not None and root is not None
        assert variant.authority_id == root_id
        assert (root.kind, variant.entity_type) == ("ORG", "PERSON")
    async with _client() as client:
        dossier = (await client.get(f"/entities/{variant_id}")).json()
        articles = (await client.get(f"/entities/{root_id}/articles")).json()
    assert (dossier["id"], dossier["entity_type"]) == (str(root_id), "ORG")
    assert [item["id"] for item in articles["items"]] == [str(article_id)]
