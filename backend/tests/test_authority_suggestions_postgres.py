import os
import uuid

import pytest
from test_entity_authority_postgres import _article

from app.db.session import session_factory
from app.nlp.models import Entity, EntityDistinct

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


def _language() -> str:
    """A language of its own, so a test sees only the entities it made."""
    return f"t{uuid.uuid4().hex[:8]}"


async def _named(db, language: str, entity_type: str, name: str, **values: object) -> Entity:  # type: ignore[no-untyped-def]
    entity = Entity(
        language=language,
        entity_type=entity_type,
        normalized_text=name,
        display_text=name.title(),
        **values,
    )
    db.add(entity)
    await db.flush()
    return entity


def _pairs(suggestions) -> set[frozenset[str]]:  # type: ignore[no-untyped-def]
    return {
        frozenset((item.root.normalized_text, item.variant.normalized_text)) for item in suggestions
    }


async def test_suggests_initials_and_acronyms_of_the_same_type() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "PERSON", "donald trump")
        await _named(db, language, "PERSON", "d. trump")
        await _named(db, language, "ORG", "european union")
        await _named(db, language, "ORG", "eu")
        # Initials of another type: never offered.
        await _named(db, language, "ORG", "d trump holdings")
        # The same name as another type is offered as such, never as an acronym of a place.
        place = await _named(db, language, "GPE", "eu")

    async with session_factory() as db:
        found = await suggest(db, language=language)

    assert _pairs(found) == {
        frozenset(("donald trump", "d. trump")),
        frozenset(("european union", "eu")),
        frozenset(("eu",)),
    }
    (same_name,) = [item for item in found if place.id in (item.root.id, item.variant.id)]
    assert same_name.reasons == ["same name, other type"]
    found = [item for item in found if item is not same_name]
    by_root = {item.root.normalized_text: item for item in found}
    # The fuller name is the one to keep when neither has more articles.
    assert by_root["donald trump"].variant.normalized_text == "d. trump"
    assert "initials" in by_root["donald trump"].reasons
    assert by_root["european union"].variant.normalized_text == "eu"
    assert "acronym" in by_root["european union"].reasons


async def test_a_bare_surname_is_suggested_unless_marked_ambiguous() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "PERSON", "angela merkel")
        await _named(db, language, "PERSON", "merkel")
        await _named(db, language, "PERSON", "olaf scholz")
        await _named(db, language, "PERSON", "scholz", ambiguous=True)

    async with session_factory() as db:
        found = await suggest(db, language=language)

    assert _pairs(found) == {frozenset(("angela merkel", "merkel"))}
    assert "surname" in found[0].reasons


async def test_places_of_both_place_types_are_paired() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "GPE", "united kingdom")
        await _named(db, language, "LOCATION", "uk")

    async with session_factory() as db:
        assert _pairs(await suggest(db, language=language)) == {frozenset(("united kingdom", "uk"))}


async def test_never_suggests_a_rejected_pair_or_a_variant() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        maria = await _named(db, language, "PERSON", "maria lopez")
        initial = await _named(db, language, "PERSON", "m. lopez")
        low, high = sorted((maria.id, initial.id))
        db.add(EntityDistinct(a_id=low, b_id=high))
        root = await _named(db, language, "PERSON", "nina park")
        await _named(db, language, "PERSON", "n. park", authority_id=root.id)

    async with session_factory() as db:
        assert await suggest(db, language=language) == []


async def test_context_ranks_shared_articles_higher() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        carl = await _named(db, language, "PERSON", "carl holm")
        c_holm = await _named(db, language, "PERSON", "c. holm")
        anna = await _named(db, language, "PERSON", "anna berg")
        a_berg = await _named(db, language, "PERSON", "a. berg")
        for _ in range(2):
            await _article(db, [(anna, [0]), (a_berg, [20])])
        await _article(db, [(carl, [0])])
        await _article(db, [(c_holm, [0])])

    async with session_factory() as db:
        found = await suggest(db, language=language)

    assert [item.root.normalized_text for item in found] == ["anna berg", "carl holm"]
    assert [item.shared_articles for item in found] == [2, 0]
    assert "shared articles" in found[0].reasons
    assert found[0].score > found[1].score


async def test_similar_spellings_of_the_same_type_are_suggested() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "ORG", "gazprom neft")
        await _named(db, language, "ORG", "gazpromneft")
        await _named(db, language, "ORG", "gazette weekly")

    async with session_factory() as db:
        found = await suggest(db, language=language)

    assert _pairs(found) == {frozenset(("gazprom neft", "gazpromneft"))}
    assert "similar spelling" in found[0].reasons


async def test_an_acronym_may_skip_or_keep_the_connecting_words() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "GPE", "united states of america")
        await _named(db, language, "GPE", "usa")
        await _named(db, language, "ORG", "bank of england")
        await _named(db, language, "ORG", "boe")

    async with session_factory() as db:
        assert _pairs(await suggest(db, language=language)) == {
            frozenset(("united states of america", "usa")),
            frozenset(("bank of england", "boe")),
        }


async def test_the_same_name_tagged_with_other_types_is_offered_once_per_pair() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        org = await _named(db, language, "ORG", "ai")
        person = await _named(db, language, "PERSON", "ai")
        product = await _named(db, language, "PRODUCT", "ai")
        # The user's own type counts: this one is shown as ORG already.
        await _named(db, language, "EVENT", "ai", preferred_type="ORG")
        await _named(db, language, "PERSON", "zed")
        ids = {org.id, person.id, product.id}

    async with session_factory() as db:
        found = await suggest(db, language=language)

    same = [item for item in found if "same name, other type" in item.reasons]
    assert len(same) == 6
    assert all(item.score >= 0.85 for item in same)
    assert {item.root.id for item in same} | {item.variant.id for item in same} >= ids


async def test_an_acronym_crosses_types_unless_a_person_meets_a_place() -> None:
    from app.entities.suggestions import suggest

    language = _language()
    async with session_factory() as db, db.begin():
        await _named(db, language, "ORG", "artificial intelligence")
        await _named(db, language, "PERSON", "ai")
        await _named(db, language, "PERSON", "new york")
        await _named(db, language, "GPE", "ny")
        await _named(db, language, "TOPIC", "machine learning")
        await _named(db, language, "PRODUCT", "ml")

    async with session_factory() as db:
        found = await suggest(db, language=language)

    assert _pairs(found) == {
        frozenset(("artificial intelligence", "ai")),
        frozenset(("machine learning", "ml")),
    }
    assert all(item.reasons == ["acronym"] for item in found)
