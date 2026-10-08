"""The local copy of Wikidata items: every page reads from here, never from Wikidata."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.wikidata.models import WikidataItem
from app.wikidata.parsing import Item

# A light fetch (a candidate's) carries no claims: it must not blank the claims already kept.
CLAIM_FIELDS = ("instance_of", "different_from", "ids", "claims_fetched")


def _row(item: Item) -> dict[str, Any]:
    return {
        "qid": item.qid,
        "state": item.state,
        "redirect_to": item.redirect_to,
        "revision": item.revision,
        "labels": item.labels,
        "aliases": item.aliases,
        "descriptions": item.descriptions,
        "instance_of": item.instance_of,
        "different_from": item.different_from,
        "ids": item.ids,
        "sitelinks": item.sitelinks,
        "claims_fetched": item.claims_fetched,
    }


async def store_items(db: AsyncSession, items: Iterable[Item]) -> None:
    """Write what Wikidata said, replacing the cached copy. The caller owns the transaction."""
    for item in items:
        row = _row(item)
        statement = insert(WikidataItem).values(**row)
        updates = {
            key: getattr(statement.excluded, key)
            for key in row
            if key != "qid" and (item.claims_fetched or key not in CLAIM_FIELDS)
        }
        if item.state != "ok":
            # A redirect or a deletion leaves nothing of the old item to keep.
            updates.update({key: getattr(statement.excluded, key) for key in CLAIM_FIELDS})
        updates["fetched_at"] = func.now()
        updates["checked_at"] = func.now()
        await db.execute(statement.on_conflict_do_update(index_elements=["qid"], set_=updates))
    await db.flush()


async def cached_items(db: AsyncSession, qids: Iterable[str]) -> dict[str, WikidataItem]:
    wanted = sorted(set(qids))
    if not wanted:
        return {}
    rows = await db.scalars(select(WikidataItem).where(WikidataItem.qid.in_(wanted)))
    return {row.qid: row for row in rows}
