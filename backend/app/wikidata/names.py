"""The names a linked item brings into the authority file.

Its labels in our languages become variants of the root on their own (decided 2026-10-08); its
aliases only when the user ticks them, since they hold nicknames and bare surnames ("Trump").
A name is stored under the same identity NER would give it, so the next article that uses it
lands on the root. A name that already belongs to another root is never moved here: a root with
articles or variants stays where it is and turns up in "Maybe the same?" as a "wikidata name".
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities import authority
from app.nlp.models import ArticleEntity, Entity
from app.nlp.processors import canonical_entity
from app.wikidata.models import WikidataItem

Kind = Literal["label", "alias"]
Status = Literal["this_entity", "other_entity", "absent"]
# The NER labels canonical_entity expects for each stored type.
_LABELS = {"LOCATION": "LOC"}
# Entity languages a Wikidata name can be stored under.
LANGUAGES = ("el", "en")


@dataclass(frozen=True)
class Name:
    language: str
    text: str
    kind: Kind


@dataclass(frozen=True)
class NameState:
    name: Name
    status: Status
    entity_id: uuid.UUID | None


def item_names(item: WikidataItem) -> list[Name]:
    """The item's labels, then its aliases, in our languages, without repeats."""
    found: dict[tuple[str, str], Name] = {}
    for language in LANGUAGES:
        label = (item.labels or {}).get(language)
        if label:
            found.setdefault((language, label), Name(language, label, "label"))
    for language in LANGUAGES:
        for alias in (item.aliases or {}).get(language, []):
            found.setdefault((language, alias), Name(language, alias, "alias"))
    return list(found.values())


def identity(root: Entity, name: Name) -> tuple[str, str, str]:
    """The (type, normalized text, display text) NER would store this name under."""
    label = _LABELS.get(root.entity_type, root.entity_type)
    return canonical_entity(name.text, label, root.entity_type, name.language)


async def _existing(db: AsyncSession, root: Entity, name: Name) -> Entity | None:
    entity_type, normalized, _ = identity(root, name)
    found: Entity | None = await db.scalar(
        select(Entity).where(
            Entity.language == name.language,
            Entity.entity_type == entity_type,
            Entity.normalized_text == normalized,
        )
    )
    return found


async def name_states(db: AsyncSession, root: Entity, item: WikidataItem) -> list[NameState]:
    states = []
    for name in item_names(item):
        existing = await _existing(db, root, name)
        if existing is None:
            states.append(NameState(name, "absent", None))
        elif existing.id == root.id or existing.authority_id == root.id:
            states.append(NameState(name, "this_entity", existing.id))
        else:
            states.append(NameState(name, "other_entity", existing.authority_id or existing.id))
    return states


async def _in_use(db: AsyncSession, entity_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    or_(
                        ArticleEntity.entity_id == entity_id,
                        ArticleEntity.observed_entity_id == entity_id,
                    )
                )
                | exists().where(Entity.authority_id == entity_id)
            )
        )
    )


async def add_names(
    db: AsyncSession, root: Entity, item: WikidataItem, chosen: list[Name]
) -> list[Name]:
    """Add the labels and the chosen names of `item` to `root`; returns the names added.

    A name no entity has is created as a variant; an unused root of that name is adopted (as an
    import does). Anything else is left alone. A change starts a run that reindexes the root's
    articles, so the new names are searchable.
    """
    wanted = [name for name in item_names(item) if name.kind == "label"]
    wanted += [name for name in chosen if name not in wanted]
    added: list[Name] = []
    for name in wanted:
        if name.language not in LANGUAGES:
            continue
        existing = await _existing(db, root, name)
        if existing is None:
            entity_type, normalized, display = identity(root, name)
            db.add(
                Entity(
                    language=name.language,
                    entity_type=entity_type,
                    normalized_text=normalized,
                    display_text=display,
                    authority_id=root.id,
                    name_source="wikidata",
                )
            )
            await db.flush()
            added.append(name)
        elif (
            existing.id != root.id
            and existing.authority_id is None
            and not existing.ambiguous
            and not await _in_use(db, existing.id)
        ):
            try:
                await authority.adopt(db, variant_id=existing.id, root_id=root.id)
            except authority.AuthorityError:
                continue
            added.append(name)
    if added:
        await authority.start_reindex(db, root.id)
    return added
