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

from sqlalchemy import cast, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clustering.models import StoryClusterMember
from app.db.session import session_factory
from app.entities.resolver import entity_group, expand_for_search, resolve_root
from app.events.models import Event, EventCluster
from app.events.service import refresh_event
from app.nlp.authority import Combined, RowPart, combine_rows, split_occurrences
from app.nlp.models import (
    ArticleEntity,
    Entity,
    EntityAuthorityChange,
    EntityAuthorityRun,
    EntityDistinct,
    EntityRelation,
)
from app.search.service import request_indexing

__all__ = [
    "AuthorityError",
    "EntityRef",
    "RowPart",
    "add_distinct",
    "adopt",
    "advance_run",
    "combine_rows",
    "entity_group",
    "expand_for_search",
    "history",
    "merge",
    "merge_problem",
    "record",
    "remove_distinct",
    "rename",
    "resolve_root",
    "scan_authority_runs",
    "set_ambiguous",
    "set_note",
    "set_status",
    "split",
    "split_occurrences",
    "variants",
]

# Decided 2026-10-08: a country the model tagged LOC may join its GPE root; the root's type wins.
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
    variant: EntityRef, root: EntityRef, *, distinct: bool, linked: bool = False
) -> tuple[int, str] | None:
    """Why `variant` may not join `root` (already resolved to a root), or None.

    Two entities with a see-also link are two things by the user's own word (Facebook, Meta).
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
    if (
        variant.entity_type != root.entity_type
        and not {
            variant.entity_type,
            root.entity_type,
        }
        <= PLACE_TYPES
    ):
        return 422, "Only entities of the same type can be merged"
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
    return EntityRef(entity.id, entity.entity_type, entity.authority_id)


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
    problem = merge_problem(
        _ref(variant),
        _ref(root),
        distinct=await _is_distinct(db, variant.id, root.id),
        linked=await _is_linked(db, variant.id, root.id),
    )
    if problem is not None:
        raise AuthorityError(*problem)
    await _move_relations(db, variant.id, root.id)
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
    problem = merge_problem(
        _ref(variant),
        _ref(root),
        distinct=await _is_distinct(db, variant.id, root.id),
        linked=await _is_linked(db, variant.id, root.id),
    )
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
    db: AsyncSession, kind: str, entity_id: uuid.UUID, root_id: uuid.UUID
) -> EntityAuthorityRun:
    run = EntityAuthorityRun(
        kind=kind, entity_id=entity_id, cursor=json.dumps({"root": str(root_id)})
    )
    db.add(run)
    await db.flush()
    return run


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
    tagged = cast(ArticleEntity.occurrences, JSONB).contains([{"entity_id": str(variant_id)}])
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
    db: AsyncSession, root_id: uuid.UUID, after: str | None, batch_size: int
) -> list[uuid.UUID]:
    """The next batch of the root's articles, each asked to be reindexed under its names now."""
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
    article_ids = await _reindex(db, root_id, cursor.get("after"), batch_size)
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
