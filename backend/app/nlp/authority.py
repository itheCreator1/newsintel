"""The authority file: roots, their variants, and the starting set of country spellings."""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.nlp.models import ArticleEntity, Entity
from app.nlp.processors import country_spellings


@dataclass(frozen=True)
class RowPart:
    """One article row's share of an entity: the name it was seen under and its mentions."""

    observed_entity_id: uuid.UUID | None
    original_label: str | None
    occurrences: list[dict[str, Any]]
    # Rows written without offsets still count their mentions.
    occurrence_count: int | None = None


@dataclass(frozen=True)
class Combined:
    observed_entity_id: uuid.UUID | None
    original_label: str | None
    occurrence_count: int
    relevance: float
    occurrences: list[dict[str, Any]]


def combine_rows(entity_id: uuid.UUID, parts: list[RowPart]) -> Combined:
    """One article row for `entity_id` out of the rows of its root and variants.

    Every mention seen under another name keeps that name's id in "entity_id", so a split can
    give it back exactly. The row's observed_entity_id is the name with the most mentions (the
    root on a tie), and its original_label the commonest label (alphabetical on a tie).
    """
    own = str(entity_id)
    occurrences: list[dict[str, Any]] = []
    names: Counter[str | None] = Counter()
    labels: Counter[str] = Counter()
    total = 0
    for part in parts:
        count = len(part.occurrences) if part.occurrence_count is None else part.occurrence_count
        total += count
        if part.original_label:
            labels[part.original_label] += count
        observed = str(part.observed_entity_id) if part.observed_entity_id else None
        if not part.occurrences:
            names[observed if observed != own else None] += count
        for occurrence in part.occurrences:
            item = dict(occurrence)
            source = item.get("entity_id") or observed
            if source and source != own:
                item["entity_id"] = source
            else:
                item.pop("entity_id", None)
            names[item.get("entity_id")] += 1
            occurrences.append(item)
    occurrences.sort(key=lambda item: int(item.get("input_start", 0)))
    name = min(names.items(), key=lambda item: (-item[1], item[0] is not None, str(item[0])))[0]
    label = min(labels.items(), key=lambda item: (-item[1], item[0]))[0] if labels else None
    return Combined(
        uuid.UUID(name) if name else None,
        label,
        total,
        min(1.0, total / 5),
        occurrences,
    )


def split_occurrences(
    occurrences: list[dict[str, Any]], variant_id: uuid.UUID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The variant's own mentions (untagged again) and everything else, in order."""
    taken: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for occurrence in occurrences:
        if occurrence.get("entity_id") == str(variant_id):
            taken.append({key: value for key, value in occurrence.items() if key != "entity_id"})
        else:
            kept.append(occurrence)
    return taken, kept


@dataclass
class SeedReport:
    created: int = 0
    linked: int = 0
    already_linked: int = 0
    # Spellings left alone because they already hold articles or variants of their own, or
    # belong to another root: tying them is a merge, which only the user does.
    skipped: list[str] = field(default_factory=list)


async def _entity(db: AsyncSession, normalized_text: str, display_text: str) -> Entity:
    await db.execute(
        insert(Entity)
        .values(
            language="en",
            entity_type="GPE",
            normalized_text=normalized_text,
            display_text=display_text,
        )
        .on_conflict_do_nothing(constraint="uq_nlp_entity_identity")
    )
    entity = await db.scalar(
        select(Entity).where(
            Entity.language == "en",
            Entity.entity_type == "GPE",
            Entity.normalized_text == normalized_text,
        )
    )
    assert entity is not None
    return entity


async def seed_countries(db: AsyncSession) -> SeedReport:
    """Tie every other spelling of a country ("USA", "America") to the country's root entity.

    Idempotent: a spelling already tied to its country is counted, not written again. The
    caller owns the transaction.
    """
    report = SeedReport()
    for code, (display, spellings) in sorted(country_spellings().items()):
        root = await _entity(db, display.casefold(), display)
        if root.authority_id is not None:
            report.skipped.append(f"{code}: {display} is itself a variant")
            continue
        for normalized, written in sorted(spellings.items()):
            existing = await db.scalar(
                select(Entity).where(
                    Entity.language == "en",
                    Entity.entity_type == "GPE",
                    Entity.normalized_text == normalized,
                )
            )
            if existing is None:
                db.add(
                    Entity(
                        language="en",
                        entity_type="GPE",
                        normalized_text=normalized,
                        display_text=written,
                        authority_id=root.id,
                    )
                )
                report.created += 1
                continue
            if existing.authority_id == root.id:
                report.already_linked += 1
                continue
            in_use = await db.scalar(
                select(
                    exists().where(ArticleEntity.entity_id == existing.id)
                    | exists().where(Entity.authority_id == existing.id)
                )
            )
            if existing.authority_id is not None or in_use:
                report.skipped.append(f"{code}: {written}")
                continue
            existing.authority_id = root.id
            report.linked += 1
        await db.flush()
    return report
