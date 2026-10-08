"""The authority file: roots, their variants, and the starting set of country spellings."""

from dataclasses import dataclass, field

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.nlp.models import ArticleEntity, Entity
from app.nlp.processors import country_spellings


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
