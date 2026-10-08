"""See-also links: relations the user states between two roots (never automatic, never merges).

Five types, each stored in one direction; the inverse is read from the other side, so the two
can never disagree. `related` is symmetric and stored once per pair (smaller id first). Links
join roots only: an id of a variant stands for its root. A link never touches the search index.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.authority import PLACE_TYPES, AuthorityError, record
from app.entities.resolver import resolve_root
from app.feeds.models import Article
from app.nlp.models import Entity, EntityRelation

__all__ = [
    "LABELS",
    "LABEL_ORDER",
    "RELATION_TYPES",
    "RelationError",
    "SeeAlso",
    "add_relation",
    "check_dates",
    "find_relation",
    "label_for",
    "labels_for",
    "oriented",
    "remove_relation",
    "resolve_label",
    "see_also",
    "type_problem",
    "update_relation",
]

RELATION_TYPES = ("succeeded_by", "part_of", "member_of", "leader_of", "related")
# (relation type, the entity asking is the subject) -> what the other entity is to it.
LABELS = {
    ("succeeded_by", True): "later_name",
    ("succeeded_by", False): "earlier_name",
    ("part_of", True): "part_of",
    ("part_of", False): "has_part",
    ("member_of", True): "member_of",
    ("member_of", False): "has_member",
    ("leader_of", True): "leader_of",
    ("leader_of", False): "led_by",
    ("related", True): "related",
    ("related", False): "related",
}
LABEL_ORDER = (
    "later_name",
    "earlier_name",
    "part_of",
    "has_part",
    "member_of",
    "has_member",
    "leader_of",
    "led_by",
    "related",
)
# Which kinds of entity each side may be; None: any. A "same" object must match the subject.
_SIDES: dict[str, tuple[frozenset[str] | None, frozenset[str] | str | None]] = {
    "succeeded_by": (frozenset({"ORG", "PLACE"}), "same"),
    "part_of": (frozenset({"ORG", "PLACE"}), "same"),
    "member_of": (frozenset({"PERSON", "ORG", "PLACE"}), frozenset({"ORG"})),
    "leader_of": (frozenset({"PERSON"}), frozenset({"ORG", "PLACE"})),
    "related": (None, None),
}
_REFUSED = {
    "succeeded_by": "Only an organisation or a place can have a later name, of the same kind",
    "part_of": "Only an organisation can be part of an organisation, and a place part of a place",
    "member_of": "Only a person, an organisation or a place can be a member, and only of an"
    " organisation",
    "leader_of": "Only a person can lead, and only an organisation or a place",
}
# Links that chain: following them must never come back to where it started.
ACYCLIC = frozenset({"succeeded_by", "part_of"})
MAX_STEPS = 10
_PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


class RelationError(AuthorityError):
    pass


def _group(entity_type: str) -> str:
    return "PLACE" if entity_type in PLACE_TYPES else entity_type


def resolve_label(label: str) -> tuple[str, bool]:
    """The stored type a label names, and whether the entity asking is its object."""
    for (relation_type, asking_is_subject), name in LABELS.items():
        if name == label:
            return relation_type, not asking_is_subject
    raise RelationError(422, f"Unknown link type: {label}")


def label_for(relation: EntityRelation, asking_id: uuid.UUID) -> str:
    return LABELS[(relation.relation_type, relation.subject_id == asking_id)]


def type_problem(relation_type: str, subject_type: str, object_type: str) -> str | None:
    subjects, objects = _SIDES[relation_type]
    subject, obj = _group(subject_type), _group(object_type)
    if subjects is not None and subject not in subjects:
        if relation_type == "part_of" and subject == "PERSON":
            return "A person cannot be part of another entity"
        return _REFUSED[relation_type]
    if objects == "same" and obj != subject:
        return _REFUSED[relation_type]
    if isinstance(objects, frozenset) and obj not in objects:
        return _REFUSED[relation_type]
    return None


def labels_for(entity_type: str) -> list[str]:
    """The labels an entity of this type can use from its own side of a link."""
    group = _group(entity_type)
    found = []
    for label in LABEL_ORDER:
        relation_type, asking_is_object = resolve_label(label)
        subjects, objects = _SIDES[relation_type]
        side = (subjects if objects == "same" else objects) if asking_is_object else subjects
        if side is None or group in side:
            found.append(label)
    return found


def _valid(value: str) -> bool:
    if not _PARTIAL_DATE.match(value):
        return False
    parts = [int(part) for part in value.split("-")]
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return False
    return True


def check_dates(valid_from: str | None, valid_to: str | None) -> None:
    """Partial dates (2009, 2021-10, 2021-10-28); a period ends no earlier than it starts,
    compared at the precision the two share ("2021-10" to "2021" is fine)."""
    for value in (valid_from, valid_to):
        if value is not None and not _valid(value):
            raise RelationError(422, f"Dates are a year, a month or a day, like 2021-10: {value}")
    if valid_from and valid_to:
        shared = min(len(valid_from), len(valid_to))
        if valid_to[:shared] < valid_from[:shared]:
            raise RelationError(422, "The period ends before it starts")


def oriented(
    relation_type: str, subject_id: uuid.UUID, object_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """The stored order: a related pair is kept once, smaller id first."""
    if relation_type == "related" and object_id < subject_id:
        return object_id, subject_id
    return subject_id, object_id


def _clean(note: str | None) -> str | None:
    return note.strip() if note and note.strip() else None


@dataclass
class SeeAlso:
    relation: EntityRelation
    label: str
    entity: Entity
    source: Article | None


async def _root(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    root_id = await resolve_root(db, entity_id)
    entity = None if root_id is None else await db.get(Entity, root_id)
    if entity is None:
        raise RelationError(404, "Entity not found")
    return entity


async def _source(db: AsyncSession, article_id: uuid.UUID | None) -> uuid.UUID | None:
    if article_id is not None and await db.get(Article, article_id) is None:
        raise RelationError(404, "Article not found")
    return article_id


async def find_relation(
    db: AsyncSession,
    relation_type: str,
    subject_id: uuid.UUID,
    object_id: uuid.UUID,
    valid_from: str | None,
) -> EntityRelation | None:
    """The stored link with this identity (the unique index's), if any."""
    subject_id, object_id = oriented(relation_type, subject_id, object_id)
    found: EntityRelation | None = await db.scalar(
        select(EntityRelation).where(
            EntityRelation.subject_id == subject_id,
            EntityRelation.relation_type == relation_type,
            EntityRelation.object_id == object_id,
            func.coalesce(EntityRelation.valid_from, "") == (valid_from or ""),
        )
    )
    return found


async def _reaches(db: AsyncSession, relation_type: str, start: uuid.UUID, goal: uuid.UUID) -> bool:
    """Whether following `relation_type` links from `start` arrives at `goal`."""
    step = select(EntityRelation.object_id.label("id"), literal(1).label("depth")).where(
        EntityRelation.relation_type == relation_type, EntityRelation.subject_id == start
    )
    walk = step.cte("walk", recursive=True)
    walk = walk.union(
        select(EntityRelation.object_id, walk.c.depth + 1)
        .join(walk, EntityRelation.subject_id == walk.c.id)
        .where(EntityRelation.relation_type == relation_type, walk.c.depth < MAX_STEPS)
    )
    return bool(await db.scalar(select(func.count()).select_from(walk).where(walk.c.id == goal)))


def _values(relation: EntityRelation) -> dict[str, Any]:
    return {
        "relation_type": relation.relation_type,
        "valid_from": relation.valid_from,
        "valid_to": relation.valid_to,
        "note": relation.note,
        "source_article_id": str(relation.source_article_id)
        if relation.source_article_id
        else None,
    }


async def add_relation(
    db: AsyncSession,
    entity_id: uuid.UUID,
    *,
    label: str,
    target_id: uuid.UUID,
    valid_from: str | None = None,
    valid_to: str | None = None,
    note: str | None = None,
    source_article_id: uuid.UUID | None = None,
) -> tuple[EntityRelation, uuid.UUID]:
    """Link the entity's root to the target's root; returns the link and the asking root's id."""
    relation_type, asking_is_object = resolve_label(label)
    root = await _root(db, entity_id)
    target = await _root(db, target_id)
    if root.id == target.id:
        raise RelationError(422, "An entity cannot be linked to itself")
    subject, obj = (target, root) if asking_is_object else (root, target)
    problem = type_problem(relation_type, subject.entity_type, obj.entity_type)
    if problem is not None:
        raise RelationError(422, problem)
    note = _clean(note)
    if relation_type == "related" and note is None:
        raise RelationError(422, "A related link needs a note saying how they relate")
    check_dates(valid_from, valid_to)
    await _source(db, source_article_id)
    if relation_type in ACYCLIC and await _reaches(db, relation_type, obj.id, subject.id):
        raise RelationError(409, "This link would make a cycle")
    if await find_relation(db, relation_type, subject.id, obj.id, valid_from) is not None:
        raise RelationError(409, "These entities already have this link")
    subject_id, object_id = oriented(relation_type, subject.id, obj.id)
    relation = EntityRelation(
        subject_id=subject_id,
        relation_type=relation_type,
        object_id=object_id,
        valid_from=valid_from,
        valid_to=valid_to,
        note=note,
        source_article_id=source_article_id,
    )
    db.add(relation)
    await db.flush()
    record(db, "relation_added", subject_id, other_id=object_id, after=_values(relation))
    await db.flush()
    return relation, root.id


async def _get(db: AsyncSession, relation_id: uuid.UUID) -> EntityRelation:
    relation = await db.get(EntityRelation, relation_id, with_for_update=True)
    if relation is None:
        raise RelationError(404, "Link not found")
    return relation


async def update_relation(
    db: AsyncSession, relation_id: uuid.UUID, changes: dict[str, Any]
) -> EntityRelation:
    """Change the dates, note or source of a link; keys left out stay as they are."""
    relation = await _get(db, relation_id)
    before = _values(relation)
    values = {
        "valid_from": relation.valid_from,
        "valid_to": relation.valid_to,
        "note": relation.note,
        "source_article_id": relation.source_article_id,
        **changes,
    }
    values["note"] = _clean(values["note"])
    check_dates(values["valid_from"], values["valid_to"])
    if relation.relation_type == "related" and values["note"] is None:
        raise RelationError(422, "A related link needs a note saying how they relate")
    await _source(db, values["source_article_id"])
    if (values["valid_from"] or "") != (relation.valid_from or ""):
        same = await find_relation(
            db,
            relation.relation_type,
            relation.subject_id,
            relation.object_id,
            values["valid_from"],
        )
        if same is not None:
            raise RelationError(409, "These entities already have this link for that start date")
    for key, value in values.items():
        setattr(relation, key, value)
    await db.flush()
    after = _values(relation)
    if after != before:
        relation.updated_at = func.now()
        record(
            db,
            "relation_changed",
            relation.subject_id,
            other_id=relation.object_id,
            before=before,
            after=after,
        )
        await db.flush()
        await db.refresh(relation)
    return relation


async def remove_relation(db: AsyncSession, relation_id: uuid.UUID) -> None:
    relation = await _get(db, relation_id)
    record(
        db,
        "relation_removed",
        relation.subject_id,
        other_id=relation.object_id,
        before=_values(relation),
    )
    await db.delete(relation)
    await db.flush()


async def see_also(db: AsyncSession, entity_id: uuid.UUID) -> list[SeeAlso]:
    """Every link of the entity's root, both ways, labelled from the root's side."""
    root = await _root(db, entity_id)
    relations = list(
        await db.scalars(
            select(EntityRelation).where(
                or_(EntityRelation.subject_id == root.id, EntityRelation.object_id == root.id)
            )
        )
    )
    others = {
        relation.object_id if relation.subject_id == root.id else relation.subject_id
        for relation in relations
    }
    sources = {relation.source_article_id for relation in relations} - {None}
    entities = {
        entity.id: entity
        for entity in await db.scalars(select(Entity).where(Entity.id.in_(others)))
    }
    articles = {
        article.id: article
        for article in await db.scalars(select(Article).where(Article.id.in_(sources)))
    }
    found = [
        SeeAlso(
            relation=relation,
            label=label_for(relation, root.id),
            entity=entities[
                relation.object_id if relation.subject_id == root.id else relation.subject_id
            ],
            source=articles.get(relation.source_article_id) if relation.source_article_id else None,
        )
        for relation in relations
    ]
    return sorted(
        found,
        key=lambda item: (
            LABEL_ORDER.index(item.label),
            item.entity.name.lower(),
            item.relation.valid_from or "",
        ),
    )
