"""The authority file as JSON: export it, and import it into this or a rebuilt database.

Entities travel by identity (language, type, normalized text), never by id, since ids differ
from one database to the next. An import creates the names it does not find, so a file
imported before NLP runs again is in place when the articles come back. It never undoes a
choice already in the database: a name already tied to another root is reported as a conflict
and left alone.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.entities import authority, relations
from app.feeds.models import Article
from app.nlp.models import ArticleEntity, Entity, EntityDistinct, EntityRelation

__all__ = ["FORMAT", "ImportReport", "export_authorities", "import_authorities", "parse_file"]

FORMAT = "newsintel-authority-file"
VERSION = 1


class _Name(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = Field(min_length=1, max_length=16)
    entity_type: str = Field(min_length=1, max_length=32)
    normalized_text: str = Field(min_length=1)
    display_text: str = Field(min_length=1)

    def label(self) -> str:
        return f"{self.normalized_text} ({self.language} {self.entity_type})"


class _Variant(_Name):
    ambiguous: bool = False
    note: str | None = None


class _Root(_Variant):
    preferred_text: str | None = None
    status: Literal["provisional", "established"] = "provisional"
    variants: list[_Variant] = Field(default_factory=list)


class _Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: _Name
    relation_type: Literal["succeeded_by", "part_of", "member_of", "leader_of", "related"]
    object: _Name
    valid_from: str | None = None
    valid_to: str | None = None
    note: str | None = None
    # The source article travels by its address, the one identity it keeps in a rebuild.
    source_url: str | None = None


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["newsintel-authority-file"]
    version: Literal[1]
    exported_at: str | None = None
    entities: list[_Root] = Field(default_factory=list)
    distinct: list[tuple[_Name, _Name]] = Field(default_factory=list)
    relations: list[_Relation] = Field(default_factory=list)


@dataclass
class ImportReport:
    created: int = 0
    merged: int = 0
    updated: int = 0
    distinct_added: int = 0
    relations_added: int = 0
    unchanged: int = 0
    conflicts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"created={self.created} merged={self.merged} updated={self.updated} "
            f"distinct_added={self.distinct_added} relations_added={self.relations_added} "
            f"unchanged={self.unchanged} "
            f"conflicts={len(self.conflicts)}"
        )


def parse_file(data: object) -> _File:
    """The file, checked; ValueError names what is wrong with it."""
    try:
        return _File.model_validate(data)
    except ValidationError as error:
        raise ValueError(f"Not a newsintel authority file: {error}") from None


def _name(entity: Entity) -> dict[str, Any]:
    return {
        "language": entity.language,
        "entity_type": entity.entity_type,
        "normalized_text": entity.normalized_text,
        "display_text": entity.display_text,
    }


def _identity(entity: Entity) -> tuple[str, str, str]:
    return entity.language, entity.entity_type, entity.normalized_text


async def export_authorities(db: AsyncSession, *, language: str | None = None) -> dict[str, Any]:
    """Every root that carries a choice (variants, a preferred name, a status, a note, ambiguity)
    with its variants, and every pair marked different."""
    variant = aliased(Entity)
    has_variants = exists().where(variant.authority_id == Entity.id)
    query = select(Entity).where(
        Entity.authority_id.is_(None),
        or_(
            has_variants,
            Entity.preferred_text.is_not(None),
            Entity.status != "provisional",
            Entity.note.is_not(None),
            Entity.ambiguous,
        ),
    )
    if language is not None:
        query = query.where(Entity.language == language)
    roots = sorted(await db.scalars(query), key=_identity)

    variants: dict[uuid.UUID, list[Entity]] = {root.id: [] for root in roots}
    for chunk_start in range(0, len(roots), 1000):
        ids = [root.id for root in roots[chunk_start : chunk_start + 1000]]
        for item in await db.scalars(select(Entity).where(Entity.authority_id.in_(ids))):
            assert item.authority_id is not None
            variants[item.authority_id].append(item)

    a, b = aliased(Entity), aliased(Entity)
    pairs = (
        select(a, b)
        .join(EntityDistinct, EntityDistinct.a_id == a.id)
        .join(b, EntityDistinct.b_id == b.id)
    )
    if language is not None:
        pairs = pairs.where(or_(a.language == language, b.language == language))
    distinct = sorted(
        (sorted((first, second), key=_identity) for first, second in await db.execute(pairs)),
        key=lambda pair: (_identity(pair[0]), _identity(pair[1])),
    )

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "entities": [
            {
                **_name(root),
                "preferred_text": root.preferred_text,
                "status": root.status,
                "ambiguous": root.ambiguous,
                "note": root.note,
                "variants": [
                    {**_name(item), "ambiguous": item.ambiguous, "note": item.note}
                    for item in sorted(variants[root.id], key=_identity)
                ],
            }
            for root in roots
        ],
        "distinct": [[_name(first), _name(second)] for first, second in distinct],
        "relations": await _export_relations(db, language),
    }


async def _export_relations(db: AsyncSession, language: str | None) -> list[dict[str, Any]]:
    """Every see-also link, by identity; a related pair reads the same way round every time."""
    subject, obj = aliased(Entity), aliased(Entity)
    query = (
        select(EntityRelation, subject, obj, Article.normalized_url)
        .join(subject, subject.id == EntityRelation.subject_id)
        .join(obj, obj.id == EntityRelation.object_id)
        .outerjoin(Article, Article.id == EntityRelation.source_article_id)
    )
    if language is not None:
        query = query.where(or_(subject.language == language, obj.language == language))
    found = []
    for relation, first, second, url in await db.execute(query):
        if relation.relation_type == "related" and _identity(second) < _identity(first):
            first, second = second, first
        found.append(
            {
                "subject": _name(first),
                "relation_type": relation.relation_type,
                "object": _name(second),
                "valid_from": relation.valid_from,
                "valid_to": relation.valid_to,
                "note": relation.note,
                "source_url": url,
            }
        )
    return sorted(
        found,
        key=lambda item: (
            tuple(item["subject"].values()),
            item["relation_type"],
            tuple(item["object"].values()),
            item["valid_from"] or "",
        ),
    )


async def _find_or_create(db: AsyncSession, name: _Name) -> tuple[Entity, bool]:
    created = await db.scalar(
        insert(Entity)
        .values(
            language=name.language,
            entity_type=name.entity_type,
            normalized_text=name.normalized_text,
            display_text=name.display_text,
        )
        .on_conflict_do_nothing(constraint="uq_nlp_entity_identity")
        .returning(Entity.id)
    )
    entity = await db.scalar(
        select(Entity)
        .where(
            Entity.language == name.language,
            Entity.entity_type == name.entity_type,
            Entity.normalized_text == name.normalized_text,
        )
        .execution_options(populate_existing=True)
    )
    assert entity is not None
    return entity, created is not None


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


async def _marks(db: AsyncSession, entity: Entity, record: _Variant) -> bool:
    """Bring the name's ambiguity and note in line with the file; True when one changed."""
    note = record.note.strip() if record.note and record.note.strip() else None
    changed = record.ambiguous != entity.ambiguous or note != entity.note
    if record.ambiguous != entity.ambiguous:
        await authority.set_ambiguous(db, entity.id, record.ambiguous)
    if note != entity.note:
        await authority.set_note(db, entity.id, note)
    return changed


async def _import_root(db: AsyncSession, record: _Root, report: ImportReport) -> None:
    root, created = await _find_or_create(db, record)
    if root.authority_id is not None:
        report.conflicts.append(
            f"{record.label()}: a variant of another name here, so its variants were skipped"
        )
        return
    changed = await _marks(db, root, record)
    preferred = " ".join(record.preferred_text.split()) if record.preferred_text else None
    if preferred != root.preferred_text:
        changed = True
        await authority.rename(db, root.id, preferred, reindex=not created)
    if record.status != root.status:
        changed = True
        await authority.set_status(db, root.id, record.status)
    if created:
        report.created += 1
    elif changed:
        report.updated += 1
    else:
        report.unchanged += 1

    for item in record.variants:
        variant, made = await _find_or_create(db, item)
        report.created += made
        if variant.authority_id == root.id:
            if await _marks(db, variant, item):
                report.updated += 1
            else:
                report.unchanged += 1
            continue
        try:
            if variant.authority_id is None and not await _in_use(db, variant.id):
                await authority.adopt(db, variant_id=variant.id, root_id=root.id)
            else:
                await authority.merge(db, variant_id=variant.id, target_id=root.id)
        except authority.AuthorityError as error:
            report.conflicts.append(f"{item.label()} under {record.label()}: {error.detail}")
            continue
        report.merged += 1
        # A conflicting name keeps its own marks; a joined one takes the file's.
        await _marks(db, variant, item)


async def _import_pair(db: AsyncSession, pair: tuple[_Name, _Name], report: ImportReport) -> None:
    first, made_first = await _find_or_create(db, pair[0])
    second, made_second = await _find_or_create(db, pair[1])
    report.created += made_first + made_second
    roots = [first.authority_id or first.id, second.authority_id or second.id]
    low, high = sorted(roots)
    if low != high and await db.get(EntityDistinct, (low, high)) is not None:
        report.unchanged += 1
        return
    try:
        await authority.add_distinct(db, first.id, second.id)
    except authority.AuthorityError as error:
        report.conflicts.append(f"{pair[0].label()} and {pair[1].label()}: {error.detail}")
        return
    report.distinct_added += 1


async def _import_relation(db: AsyncSession, item: _Relation, report: ImportReport) -> None:
    first, made_first = await _find_or_create(db, item.subject)
    second, made_second = await _find_or_create(db, item.object)
    report.created += made_first + made_second
    subject_id = first.authority_id or first.id
    object_id = second.authority_id or second.id
    if await relations.find_relation(
        db, item.relation_type, subject_id, object_id, item.valid_from
    ):
        report.unchanged += 1
        return
    source_id = (
        await db.scalar(select(Article.id).where(Article.normalized_url == item.source_url))
        if item.source_url
        else None
    )
    try:
        await relations.add_relation(
            db,
            subject_id,
            label=relations.LABELS[(item.relation_type, True)],
            target_id=object_id,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            note=item.note,
            source_article_id=source_id,
        )
    except authority.AuthorityError as error:
        report.conflicts.append(
            f"{item.subject.label()} {item.relation_type} {item.object.label()}: {error.detail}"
        )
        return
    report.relations_added += 1


async def import_authorities(db: AsyncSession, data: object) -> ImportReport:
    """Apply a file; the caller owns the transaction, so a dry run is a rollback."""
    parsed = parse_file(data)
    report = ImportReport()
    for record in parsed.entities:
        await _import_root(db, record, report)
    for pair in parsed.distinct:
        await _import_pair(db, pair, report)
    for item in parsed.relations:
        await _import_relation(db, item, report)
    await db.flush()
    return report
