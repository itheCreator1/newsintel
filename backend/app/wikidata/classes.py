"""Does an item's class fit a root's type? Climb P279 (subclass of), keeping every class seen.

A party is an instance of "political party", which is a subclass of "organization" a few levels
up. Each class is fetched once and kept for wikidata_class_cache_days; there are only hundreds.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.wikidata.client import BATCH, WikidataClient
from app.wikidata.models import WikidataClass
from app.wikidata.types import SUBCLASS_DEPTH, TYPE_ROOTS


async def _known(db: AsyncSession, qids: Iterable[str], settings: Settings) -> dict[str, list[str]]:
    fresh = datetime.now(UTC) - timedelta(days=settings.wikidata_class_cache_days)
    rows = await db.execute(
        select(WikidataClass.qid, WikidataClass.subclass_of).where(
            WikidataClass.qid.in_(sorted(set(qids))), WikidataClass.fetched_at >= fresh
        )
    )
    return {qid: list(parents) for qid, parents in rows}


async def _fetch(db: AsyncSession, client: WikidataClient, qids: list[str]) -> None:
    """Fetch classes with their claims (sizes checked first) and keep what they are under."""
    for item in await client.items(qids, full=True):
        # A merged class stands under the class it became; a deleted one under nothing.
        parents = [item.redirect_to] if item.redirect_to else item.subclass_of
        statement = insert(WikidataClass).values(
            qid=item.qid, label_en=item.labels.get("en"), subclass_of=parents
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["qid"],
                set_={
                    "label_en": statement.excluded.label_en,
                    "subclass_of": statement.excluded.subclass_of,
                    "fetched_at": func.now(),
                },
            )
        )
    # Kept at once: a pause on the next request must not cost these again.
    await db.commit()


async def type_matches(
    db: AsyncSession,
    client: WikidataClient,
    entity_type: str,
    classes: Iterable[str],
    settings: Settings,
) -> bool | None:
    """True when a class is, or is under, one of the type's roots; None when it cannot say.

    Each level asks for its unknown classes in one batch of at most 50; a level wider than that
    is cut, and a "no" from a cut tree is "cannot say".
    """
    roots = set(TYPE_ROOTS.get(entity_type, ()))
    frontier = set(classes)
    if not roots or not frontier:
        return None
    seen: set[str] = set()
    cut = False
    for depth in range(SUBCLASS_DEPTH + 1):
        if frontier & roots:
            return True
        frontier -= seen
        if depth == SUBCLASS_DEPTH or not frontier:
            break
        seen |= frontier
        if len(frontier) > BATCH:
            frontier = set(sorted(frontier)[:BATCH])
            cut = True
        known = await _known(db, frontier, settings)
        missing = sorted(frontier - set(known))
        if missing:
            await _fetch(db, client, missing)
            known.update(await _known(db, missing, settings))
        frontier = {parent for qid in frontier for parent in known.get(qid, [])}
    return None if cut else False
