"""Authority control: tie a variant to a root, take it back, and move the article links.

The user decides; nothing here runs on its own except the batched runs a merge or split starts.
A variant always points straight at a root, never at another variant. Article rows always name
the root in `entity_id` and keep the name the article used in `observed_entity_id` and, per
mention, in each occurrence's "entity_id", so a split can give the mentions back exactly.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryClusterMember
from app.db.session import session_factory
from app.entities.resolver import entity_group, expand_for_search, resolve_root
from app.events.models import Event, EventCluster
from app.events.service import refresh_event
from app.nlp.authority import Combined, RowPart, combine_rows, split_occurrences
from app.nlp.models import (
    ENTITY_TYPES,
    ArticleEntity,
    Entity,
    EntityAuthorityChange,
    EntityAuthorityRun,
    EntityDistinct,
    EntityNameClaim,
    EntityRelation,
)
from app.search.service import request_indexing
from app.wikidata.models import EntityExternalId

__all__ = [
    "AuthorityError",
    "ClaimResult",
    "EntityRef",
    "RowPart",
    "add_distinct",
    "adopt",
    "claim_name",
    "advance_run",
    "bulk",
    "combine_rows",
    "entity_group",
    "expand_for_search",
    "history",
    "merge",
    "merge_problem",
    "record",
    "release_name",
    "remove_distinct",
    "rename",
    "resolve_root",
    "scan_authority_runs",
    "set_ambiguous",
    "set_hidden",
    "set_note",
    "set_status",
    "set_type",
    "split",
    "split_occurrences",
    "start_reindex",
    "variants",
]

# GPE and LOCATION are one group for the suggestions and the see-also rules.
PLACE_TYPES = frozenset({"GPE", "LOCATION"})
STATUSES = ("provisional", "established")
# Articles moved per run per scheduler cycle, as NLP reprocessing does.
RUN_BATCH = 100


class AuthorityError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class EntityRef:
    id: uuid.UUID
    entity_type: str
    authority_id: uuid.UUID | None


def merge_problem(
    variant: EntityRef,
    root: EntityRef,
    *,
    distinct: bool,
    linked: bool = False,
    qids: tuple[str | None, str | None] = (None, None),
) -> tuple[int, str] | None:
    """Why `variant` may not join `root` (already resolved to a root), or None.

    Two entities with a see-also link are two things by the user's own word (Facebook, Meta);
    two linked to different Wikidata items are two things by Wikidata's (`qids`: variant, root).
    """
    if variant.id == root.id:
        return 422, "An entity cannot be merged into itself"
    if variant.authority_id is not None:
        if variant.authority_id == root.id:
            return 409, "This entity is already a variant of that one"
        return 409, "This entity is a variant of another; split it first"
    if distinct:
        return 409, "These entities are marked as different"
    if linked:
        return 409, "These entities are linked by a see-also relation; remove it first"
    if qids[0] is not None and qids[1] is not None and qids[0] != qids[1]:
        return (
            409,
            f"Wikidata says these are two different things ({qids[0]} and {qids[1]});"
            " unlink one of them first",
        )
    # Decided 2026-10-11: the type is no bar ("AI" tagged PERSON joins "AI" the ORG); the
    # root's type is the one that counts afterwards.
    return None


async def variants(db: AsyncSession, root_id: uuid.UUID) -> list[Entity]:
    return list(
        await db.scalars(
            select(Entity)
            .where(Entity.authority_id == root_id)
            .order_by(Entity.normalized_text, Entity.id)
        )
    )


async def history(db: AsyncSession, root_id: uuid.UUID) -> list[EntityAuthorityChange]:
    """Every change to a root or to a name that is its variant now, oldest first."""
    ids = [root_id, *(variant.id for variant in await variants(db, root_id))]
    return list(
        await db.scalars(
            select(EntityAuthorityChange)
            .where(
                or_(
                    EntityAuthorityChange.entity_id.in_(ids),
                    EntityAuthorityChange.other_id.in_(ids),
                )
            )
            .order_by(EntityAuthorityChange.created_at, EntityAuthorityChange.id)
        )
    )


def _ref(entity: Entity) -> EntityRef:
    return EntityRef(entity.id, entity.kind, entity.authority_id)


async def _locked(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    """The entity, its row locked: NER's upsert of the same name waits for this transaction."""
    entity = await db.scalar(
        select(Entity)
        .where(Entity.id == entity_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if entity is None:
        raise AuthorityError(404, "Entity not found")
    return entity


async def _get(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    entity = await db.get(Entity, entity_id)
    if entity is None:
        raise AuthorityError(404, "Entity not found")
    return entity


async def _root_of(db: AsyncSession, entity: Entity) -> Entity:
    return entity if entity.authority_id is None else await _get(db, entity.authority_id)


def _pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    # Postgres orders uuids bytewise, as Python does.
    return (a, b) if a < b else (b, a)


async def _is_distinct(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    low, high = _pair(a, b)
    return bool(
        await db.scalar(
            select(exists().where(EntityDistinct.a_id == low, EntityDistinct.b_id == high))
        )
    )


async def _is_linked(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    or_(
                        (EntityRelation.subject_id == a) & (EntityRelation.object_id == b),
                        (EntityRelation.subject_id == b) & (EntityRelation.object_id == a),
                    )
                )
            )
        )
    )


async def _qid(db: AsyncSession, entity_id: uuid.UUID) -> str | None:
    qid: str | None = await db.scalar(
        select(EntityExternalId.value).where(
            EntityExternalId.entity_id == entity_id, EntityExternalId.scheme == "wikidata"
        )
    )
    return qid


async def _problem(db: AsyncSession, variant: Entity, root: Entity) -> tuple[int, str] | None:
    return merge_problem(
        _ref(variant),
        _ref(root),
        distinct=await _is_distinct(db, variant.id, root.id),
        linked=await _is_linked(db, variant.id, root.id),
        qids=(await _qid(db, variant.id), await _qid(db, root.id)),
    )


async def _move_external_ids(db: AsyncSession, variant_id: uuid.UUID, root_id: uuid.UUID) -> None:
    """A merged root's Wikidata link and identifiers go to its new root, which had none."""
    qid = await _qid(db, variant_id)
    if qid is None:
        return
    await db.execute(
        update(EntityExternalId)
        .where(EntityExternalId.entity_id == variant_id)
        .values(entity_id=root_id)
    )
    _record(
        db,
        "wikidata_linked",
        root_id,
        other_id=variant_id,
        after={"qid": qid, "moved_from": str(variant_id)},
    )


async def _move_claims(db: AsyncSession, variant_id: uuid.UUID, root_id: uuid.UUID) -> None:
    """A merged root's name claims go with it: claims always name a root."""
    await db.execute(
        update(EntityNameClaim).where(EntityNameClaim.root_id == variant_id).values(root_id=root_id)
    )


async def _move_relations(db: AsyncSession, variant_id: uuid.UUID, root_id: uuid.UUID) -> None:
    """The variant's see-also links become the root's; one the root already has is dropped."""
    moving = list(
        await db.scalars(
            select(EntityRelation)
            .where(
                or_(EntityRelation.subject_id == variant_id, EntityRelation.object_id == variant_id)
            )
            .with_for_update()
        )
    )
    for relation in moving:
        subject = root_id if relation.subject_id == variant_id else relation.subject_id
        obj = root_id if relation.object_id == variant_id else relation.object_id
        if relation.relation_type == "related" and obj < subject:
            subject, obj = obj, subject
        same = await db.scalar(
            select(
                exists().where(
                    EntityRelation.id != relation.id,
                    EntityRelation.subject_id == subject,
                    EntityRelation.relation_type == relation.relation_type,
                    EntityRelation.object_id == obj,
                    func.coalesce(EntityRelation.valid_from, "") == (relation.valid_from or ""),
                )
            )
        )
        if same:
            await db.delete(relation)
        else:
            relation.subject_id, relation.object_id = subject, obj
        await db.flush()


def _record(
    db: AsyncSession,
    action: str,
    entity_id: uuid.UUID,
    *,
    other_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        EntityAuthorityChange(
            action=action,
            entity_id=entity_id,
            other_id=other_id,
            before=before,
            after=after,
            # now() is the transaction's start, so several changes in one request would tie.
            created_at=func.clock_timestamp(),
        )
    )


# The history writer, for the see-also links that live in their own module.
record = _record


async def merge(
    db: AsyncSession, *, variant_id: uuid.UUID, target_id: uuid.UUID
) -> EntityAuthorityRun:
    """Make `variant_id` a variant of `target_id`'s root and start moving its article links."""
    variant = await _locked(db, variant_id)
    root = await _root_of(db, await _get(db, target_id))
    problem = await _problem(db, variant, root)
    if problem is not None:
        raise AuthorityError(*problem)
    await _move_relations(db, variant.id, root.id)
    await _move_external_ids(db, variant.id, root.id)
    await _move_claims(db, variant.id, root.id)
    # The variant's own variants follow it to the new root: never a chain.
    await db.execute(
        update(Entity).where(Entity.authority_id == variant.id).values(authority_id=root.id)
    )
    variant.authority_id = root.id
    _record(
        db,
        "merged",
        variant.id,
        other_id=root.id,
        before={"authority_id": None},
        after={"authority_id": str(root.id)},
    )
    return await _start_run(db, "merge", variant.id, root.id)


async def adopt(db: AsyncSession, *, variant_id: uuid.UUID, root_id: uuid.UUID) -> None:
    """Tie a name no article uses yet to a root: there is nothing to move, so no run.

    For an import into a database that has not seen the name; anything else is a `merge`.
    """
    variant = await _locked(db, variant_id)
    root = await _get(db, root_id)
    problem = await _problem(db, variant, root)
    if problem is not None:
        raise AuthorityError(*problem)
    in_use = await db.scalar(
        select(
            exists().where(
                or_(
                    ArticleEntity.entity_id == variant.id,
                    ArticleEntity.observed_entity_id == variant.id,
                )
            )
            | exists().where(Entity.authority_id == variant.id)
        )
    )
    if in_use:
        raise AuthorityError(409, "This entity has articles or variants; merge it instead")
    await _move_relations(db, variant.id, root.id)
    await _move_external_ids(db, variant.id, root.id)
    await _move_claims(db, variant.id, root.id)
    variant.authority_id = root.id
    _record(
        db,
        "merged",
        variant.id,
        other_id=root.id,
        before={"authority_id": None},
        after={"authority_id": str(root.id)},
    )
    await db.flush()


async def split(db: AsyncSession, variant_id: uuid.UUID) -> EntityAuthorityRun:
    """Make a variant its own root again and start giving its mentions back."""
    variant = await _locked(db, variant_id)
    root_id = variant.authority_id
    if root_id is None:
        raise AuthorityError(409, "This entity is not a variant")
    variant.authority_id = None
    _record(
        db,
        "split",
        variant.id,
        other_id=root_id,
        before={"authority_id": str(root_id)},
        after={"authority_id": None},
    )
    return await _start_run(db, "split", variant.id, root_id)


async def _start_run(
    db: AsyncSession,
    kind: str,
    entity_id: uuid.UUID,
    root_id: uuid.UUID,
    *,
    events: bool = False,
) -> EntityAuthorityRun:
    """`events`: the root's events are recomputed too, as each batch is reindexed."""
    cursor: dict[str, Any] = {"root": str(root_id)}
    if events:
        cursor["events"] = True
    run = EntityAuthorityRun(kind=kind, entity_id=entity_id, cursor=json.dumps(cursor))
    db.add(run)
    await db.flush()
    return run


async def start_reindex(db: AsyncSession, root_id: uuid.UUID) -> EntityAuthorityRun:
    """Reindex a root's articles in batches, after its names changed outside a rename."""
    return await _start_run(db, "reindex", root_id, root_id)


async def _root_only(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    entity = await _locked(db, entity_id)
    if entity.authority_id is not None:
        raise AuthorityError(409, "This entity is a variant; change its root instead")
    return entity


async def rename(
    db: AsyncSession, entity_id: uuid.UUID, preferred_text: str | None, *, reindex: bool = True
) -> EntityAuthorityRun | None:
    """Set the root's preferred name; None goes back to the latest spelling NLP saw.

    The search index carries the name, so a change starts a run that reindexes the root's
    articles; the same name again changes nothing and returns None. A name no article uses
    yet (`reindex=False`, for an import) needs no run.
    """
    entity = await _root_only(db, entity_id)
    value = " ".join(preferred_text.split()) if preferred_text else None
    if value == entity.preferred_text:
        return None
    _record(
        db,
        "renamed",
        entity.id,
        before={"preferred_text": entity.preferred_text},
        after={"preferred_text": value},
    )
    entity.preferred_text = value
    if not reindex:
        await db.flush()
        return None
    return await _start_run(db, "reindex", entity.id, entity.id)


async def set_type(
    db: AsyncSession, entity_id: uuid.UUID, entity_type: str | None
) -> EntityAuthorityRun | None:
    """Show the root as `entity_type`; None, or the model's own type, goes back to the model's.

    The search index and the events carry the type, so a change starts a run that reindexes the
    root's articles and recomputes their events; the same type again changes nothing.
    """
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        raise AuthorityError(422, f"Type must be one of: {', '.join(ENTITY_TYPES)}")
    entity = await _root_only(db, entity_id)
    value = None if entity_type == entity.entity_type else entity_type
    if value == entity.preferred_type:
        return None
    _record(
        db,
        "type_changed",
        entity.id,
        before={"entity_type": entity.kind},
        after={"entity_type": value or entity.entity_type},
    )
    entity.preferred_type = value
    return await _start_run(db, "reindex", entity.id, entity.id, events=True)


async def set_hidden(
    db: AsyncSession, entity_id: uuid.UUID, hidden: bool
) -> EntityAuthorityRun | None:
    """Hide a root that is not a name, or show it again; nothing about it is deleted.

    Its articles are reindexed without it (or with it again) and their events recomputed.
    """
    entity = await _root_only(db, entity_id)
    if hidden == entity.hidden:
        return None
    _record(
        db, "hidden_changed", entity.id, before={"hidden": entity.hidden}, after={"hidden": hidden}
    )
    entity.hidden = hidden
    return await _start_run(db, "reindex", entity.id, entity.id, events=True)


@dataclass(frozen=True)
class Skipped:
    entity_id: uuid.UUID
    reason: str


@dataclass
class ClaimResult:
    runs: list[EntityAuthorityRun]
    skipped: list[Skipped]


async def claim_name(
    db: AsyncSession, root_id: uuid.UUID, normalized_text: str | None = None
) -> ClaimResult:
    """Make the root take every type of one of its names ("AI" the ORG, PERSON and PRODUCT).

    The name is the root's own unless `normalized_text` names one of its variants. Every other
    root of that name merges in now, unless it may not (marked different, linked, another
    Wikidata item); a name already under another root stays there. Both are reported. From
    then on NER puts a name it finds under a new type straight under the root.
    """
    root = await _root_only(db, root_id)
    if root.ambiguous:
        raise AuthorityError(
            409, "An ambiguous name may stand for several things; it cannot take all its types"
        )
    key = normalized_text if normalized_text is not None else root.normalized_text
    own = key == root.normalized_text or await db.scalar(
        select(
            exists().where(
                Entity.authority_id == root.id,
                Entity.language == root.language,
                Entity.normalized_text == key,
            )
        )
    )
    if not own:
        raise AuthorityError(422, "Only one of this entity's own names can be claimed")
    claim = await db.get(EntityNameClaim, (root.language, key))
    if claim is not None and claim.root_id != root.id:
        raise AuthorityError(409, "Another entity already takes every type of this name")
    result = ClaimResult(runs=[], skipped=[])
    others = list(
        await db.scalars(
            select(Entity)
            .where(
                Entity.language == root.language,
                Entity.normalized_text == key,
                Entity.id != root.id,
                or_(Entity.authority_id.is_(None), Entity.authority_id != root.id),
            )
            .order_by(Entity.entity_type, Entity.id)
        )
    )
    for other in others:
        if other.authority_id is not None:
            holder = await _get(db, other.authority_id)
            result.skipped.append(
                Skipped(
                    other.id, f"Already a name of another entity, {holder.name}; split it first"
                )
            )
            continue
        problem = await _problem(db, other, root)
        if problem is not None:
            result.skipped.append(Skipped(other.id, problem[1]))
            continue
        result.runs.append(await merge(db, variant_id=other.id, target_id=root.id))
    if claim is None:
        db.add(EntityNameClaim(language=root.language, normalized_text=key, root_id=root.id))
        _record(db, "claim_added", root.id, after={"normalized_text": key})
    await db.flush()
    return result


async def release_name(db: AsyncSession, root_id: uuid.UUID, normalized_text: str) -> bool:
    """Stop taking every type of the name; what merged stays merged. False if no such claim."""
    root = await _root_only(db, root_id)
    removed = await db.scalar(
        delete(EntityNameClaim)
        .where(
            EntityNameClaim.language == root.language,
            EntityNameClaim.normalized_text == normalized_text,
            EntityNameClaim.root_id == root.id,
        )
        .returning(EntityNameClaim.root_id)
    )
    if removed is None:
        return False
    _record(db, "claim_removed", root.id, before={"normalized_text": normalized_text})
    await db.flush()
    return True


async def claims_of(db: AsyncSession, root_id: uuid.UUID) -> list[str]:
    return list(
        await db.scalars(
            select(EntityNameClaim.normalized_text)
            .where(EntityNameClaim.root_id == root_id)
            .order_by(EntityNameClaim.normalized_text)
        )
    )


async def other_types(db: AsyncSession, root: Entity) -> list[Entity]:
    """Other roots spelled like one of this root's names, under another type: what a claim of
    that name would bring in."""
    group = select(Entity.normalized_text).where(
        or_(Entity.id == root.id, Entity.authority_id == root.id)
    )
    return list(
        await db.scalars(
            select(Entity)
            .where(
                Entity.language == root.language,
                Entity.normalized_text.in_(group),
                Entity.authority_id.is_(None),
                Entity.id != root.id,
                Entity.hidden.is_(False),
            )
            .order_by(Entity.normalized_text, Entity.entity_type, Entity.id)
        )
    )


async def bulk(
    db: AsyncSession,
    action: str,
    ids: list[uuid.UUID],
    *,
    target_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    status: str | None = None,
) -> list[EntityAuthorityRun]:
    """One change to several entities, in the caller's transaction: the first refusal raises,
    naming the entity, and the caller rolls the rest back."""
    runs: list[EntityAuthorityRun] = []
    for entity_id in dict.fromkeys(ids):
        entity = await db.get(Entity, entity_id)
        label = entity.name if entity is not None else str(entity_id)
        try:
            run: EntityAuthorityRun | None = None
            if action == "merge":
                assert target_id is not None
                if entity_id == target_id:
                    continue
                run = await merge(db, variant_id=entity_id, target_id=target_id)
            elif action == "set_type":
                run = await set_type(db, entity_id, entity_type)
            elif action == "set_status":
                assert status is not None
                await set_status(db, entity_id, status)
            elif action in ("hide", "show"):
                run = await set_hidden(db, entity_id, action == "hide")
            else:
                raise AuthorityError(422, f"Unknown action: {action}")
        except AuthorityError as error:
            raise AuthorityError(error.status_code, f"{label}: {error.detail}") from None
        if run is not None:
            runs.append(run)
    return runs


async def set_status(db: AsyncSession, entity_id: uuid.UUID, status: str) -> Entity:
    if status not in STATUSES:
        raise AuthorityError(422, f"Status must be one of: {', '.join(STATUSES)}")
    entity = await _root_only(db, entity_id)
    if status != entity.status:
        _record(
            db,
            "status_changed",
            entity.id,
            before={"status": entity.status},
            after={"status": status},
        )
        entity.status = status
        await db.flush()
    return entity


async def set_ambiguous(db: AsyncSession, entity_id: uuid.UUID, ambiguous: bool) -> Entity:
    """Mark a name that may stand for several people; NER then never folds it into a longer one."""
    entity = await _locked(db, entity_id)
    if ambiguous != entity.ambiguous:
        _record(
            db,
            "ambiguous_changed",
            entity.id,
            before={"ambiguous": entity.ambiguous},
            after={"ambiguous": ambiguous},
        )
        entity.ambiguous = ambiguous
        await db.flush()
    return entity


async def set_note(db: AsyncSession, entity_id: uuid.UUID, note: str | None) -> Entity:
    entity = await _locked(db, entity_id)
    entity.note = note.strip() if note and note.strip() else None
    await db.flush()
    return entity


async def add_distinct(db: AsyncSession, a_id: uuid.UUID, b_id: uuid.UUID) -> None:
    """Record that two roots are different, so neither merges into the other by mistake."""
    if a_id == b_id:
        raise AuthorityError(422, "An entity cannot be different from itself")
    roots = [await _root_of(db, await _get(db, value)) for value in (a_id, b_id)]
    if roots[0].id == roots[1].id:
        raise AuthorityError(409, "These are already one entity; split them first")
    low, high = _pair(roots[0].id, roots[1].id)
    added = await db.scalar(
        insert(EntityDistinct)
        .values(a_id=low, b_id=high)
        .on_conflict_do_nothing()
        .returning(EntityDistinct.a_id)
    )
    if added is not None:
        _record(db, "distinct_added", low, other_id=high)
        await db.flush()


async def remove_distinct(db: AsyncSession, a_id: uuid.UUID, b_id: uuid.UUID) -> None:
    roots = [await _root_of(db, await _get(db, value)) for value in (a_id, b_id)]
    low, high = _pair(roots[0].id, roots[1].id)
    removed = await db.scalar(
        delete(EntityDistinct)
        .where(EntityDistinct.a_id == low, EntityDistinct.b_id == high)
        .returning(EntityDistinct.a_id)
    )
    if removed is not None:
        _record(db, "distinct_removed", low, other_id=high)
        await db.flush()


def _part(row: ArticleEntity, observed: uuid.UUID | None) -> RowPart:
    return RowPart(observed, row.original_label, list(row.occurrences or []), row.occurrence_count)


def _apply(row: ArticleEntity, entity_id: uuid.UUID, combined: Combined) -> None:
    row.entity_id = entity_id
    row.observed_entity_id = combined.observed_entity_id
    row.original_label = combined.original_label
    row.occurrence_count = combined.occurrence_count
    row.relevance = combined.relevance
    row.occurrences = combined.occurrences


async def _merge_article(
    db: AsyncSession, article_id: uuid.UUID, variant_id: uuid.UUID, root_id: uuid.UUID
) -> None:
    rows = list(
        await db.scalars(
            select(ArticleEntity)
            .where(
                ArticleEntity.article_id == article_id,
                ArticleEntity.entity_id.in_((variant_id, root_id)),
            )
            .order_by(ArticleEntity.id)
            .with_for_update()
        )
    )
    # Rows of earlier generations move too, so no row names the variant as a root any more.
    for is_current in (True, False):
        group = [row for row in rows if row.is_current is is_current]
        parts = [
            _part(
                row, row.observed_entity_id or (variant_id if row.entity_id == variant_id else None)
            )
            for row in group
        ]
        if not group:
            continue
        if not is_current:
            for row, part in zip(group, parts, strict=True):
                _apply(row, root_id, combine_rows(root_id, [part]))
            continue
        # One current row per article and root, as NER writes it.
        keep = next((row for row in group if row.entity_id == root_id), group[0])
        _apply(keep, root_id, combine_rows(root_id, parts))
        for row in group:
            if row is not keep:
                await db.delete(row)


async def _split_article(
    db: AsyncSession, article_id: uuid.UUID, variant_id: uuid.UUID, root_id: uuid.UUID
) -> None:
    rows = list(
        await db.scalars(
            _split_rows(variant_id, root_id).where(ArticleEntity.article_id == article_id)
        )
    )
    for row in rows:
        # Tagged mentions say exactly whose they are and untagged ones are the root's. A row
        # with no tags at all was written under one name: its observed one.
        tagged = any("entity_id" in item for item in row.occurrences or [])
        normalized = combine_rows(root_id, [_part(row, None if tagged else row.observed_entity_id)])
        if not normalized.occurrences:
            # Stored without offsets: the row is the variant's when it was seen under it.
            _apply(row, variant_id, combine_rows(variant_id, [_part(row, None)]))
            continue
        taken, kept = split_occurrences(normalized.occurrences, variant_id)
        label = row.original_label
        if not kept:
            _apply(row, variant_id, combine_rows(variant_id, [RowPart(None, label, taken)]))
            continue
        _apply(row, root_id, combine_rows(root_id, [RowPart(None, label, kept)]))
        given_back = combine_rows(variant_id, [RowPart(None, label, taken)])
        db.add(
            ArticleEntity(
                article_id=row.article_id,
                entity_id=variant_id,
                observed_entity_id=None,
                run_id=row.run_id,
                original_label=given_back.original_label,
                occurrence_count=given_back.occurrence_count,
                relevance=given_back.relevance,
                occurrences=given_back.occurrences,
                input_fingerprint=row.input_fingerprint,
                is_current=row.is_current,
            )
        )


def _split_rows(variant_id: uuid.UUID, root_id: uuid.UUID):  # type: ignore[no-untyped-def]
    tagged = ArticleEntity.occurrences.contains([{"entity_id": str(variant_id)}])
    return select(ArticleEntity).where(
        ArticleEntity.entity_id == root_id,
        or_(ArticleEntity.observed_entity_id == variant_id, tagged),
    )


async def _touch(db: AsyncSession, article_ids: list[uuid.UUID], *, reindex: bool) -> None:
    """Recompute the events these articles belong to, and reindex them when asked."""
    event_ids = list(
        (
            await db.scalars(
                select(EventCluster.event_id)
                .join(StoryClusterMember, StoryClusterMember.cluster_id == EventCluster.cluster_id)
                .where(StoryClusterMember.article_id.in_(article_ids))
                .distinct()
            )
        ).all()
    )
    for event_id in sorted(event_ids):
        event = await db.get(Event, event_id)
        if event is not None:
            await refresh_event(db, event)
    if reindex:
        for article_id in article_ids:
            await request_indexing(db, article_id)


async def _move(
    db: AsyncSession, run: EntityAuthorityRun, root_id: uuid.UUID, batch_size: int
) -> list[uuid.UUID]:
    if run.kind == "merge":
        pending = select(ArticleEntity.article_id).where(ArticleEntity.entity_id == run.entity_id)
    else:
        pending = _split_rows(run.entity_id, root_id).with_only_columns(ArticleEntity.article_id)
    article_ids = list(
        (
            await db.scalars(
                pending.group_by(ArticleEntity.article_id)
                .order_by(ArticleEntity.article_id)
                .limit(batch_size)
            )
        ).all()
    )
    for article_id in article_ids:
        if run.kind == "merge":
            await _merge_article(db, article_id, run.entity_id, root_id)
        else:
            await _split_article(db, article_id, run.entity_id, root_id)
    await db.flush()
    if article_ids:
        # Merged articles belong to the root now and are reindexed with its other articles;
        # given-back ones belong to the variant, so they are reindexed here.
        await _touch(db, article_ids, reindex=run.kind == "split")
    return article_ids


async def _reindex(
    db: AsyncSession, root_id: uuid.UUID, after: str | None, batch_size: int, *, events: bool
) -> list[uuid.UUID]:
    """The next batch of the root's articles, each asked to be reindexed under its names now,
    with their events recomputed when the run asks for it."""
    query = select(ArticleEntity.article_id).where(
        ArticleEntity.entity_id == root_id, ArticleEntity.is_current.is_(True)
    )
    if after is not None:
        query = query.where(ArticleEntity.article_id > uuid.UUID(after))
    article_ids = list(
        (
            await db.scalars(
                query.group_by(ArticleEntity.article_id)
                .order_by(ArticleEntity.article_id)
                .limit(batch_size)
            )
        ).all()
    )
    if events and article_ids:
        await _touch(db, article_ids, reindex=True)
    else:
        for article_id in article_ids:
            await request_indexing(db, article_id)
    return article_ids


async def advance_run(db: AsyncSession, run_id: uuid.UUID, *, batch_size: int = RUN_BATCH) -> int:
    """Work through one batch of articles; a batch that finds nothing left finishes the run.

    A merge or split moves rows until none are left, then reindexes the root's articles, whose
    names changed; a reindex run does only that. The cursor keeps the phase and the last
    article reindexed, so a run resumes where it stopped.
    """
    run = await db.scalar(
        select(EntityAuthorityRun)
        .where(EntityAuthorityRun.id == run_id)
        .with_for_update(skip_locked=True)
    )
    if run is None or run.status != "running":
        return 0
    cursor = json.loads(run.cursor or "{}")
    root_id = uuid.UUID(cursor["root"])
    phase = cursor.get("phase", "reindex" if run.kind == "reindex" else "move")
    if phase == "move":
        moved = await _move(db, run, root_id, batch_size)
        if moved:
            return len(moved)
        phase, cursor["after"] = "reindex", None
    article_ids = await _reindex(
        db, root_id, cursor.get("after"), batch_size, events=bool(cursor.get("events"))
    )
    if article_ids:
        cursor.update(phase=phase, after=str(article_ids[-1]))
        run.cursor = json.dumps(cursor)
    else:
        run.status = "finished"
        run.finished_at = datetime.now(UTC)
    await db.flush()
    return len(article_ids)


async def scan_authority_runs(batch_size: int = RUN_BATCH) -> int:
    """Advance every unfinished run by one batch; the scheduler calls this each cycle."""
    async with session_factory() as db:
        run_ids = list(
            (
                await db.scalars(
                    select(EntityAuthorityRun.id)
                    .where(EntityAuthorityRun.status == "running")
                    .order_by(EntityAuthorityRun.created_at)
                )
            ).all()
        )
    moved = 0
    for run_id in run_ids:
        async with session_factory() as db, db.begin():
            moved += await advance_run(db, run_id, batch_size=batch_size)
    return moved
