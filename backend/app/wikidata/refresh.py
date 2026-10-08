"""Keep linked items current: ask their revisions, fetch again only the items that changed.

Each linked item is checked every wikidata_refresh_days with `prop=info`, 50 to a request; only
an item with a new revision is fetched again, in full. What the refresh finds:

- a new label or alias updates the cache only: the entity page offers it, nothing is added;
- a redirect (Wikidata merged two items) moves the link to the item it became, with history,
  unless another root holds that item: then nothing changes and the page offers the merge;
- a deleted item keeps its link and is marked missing, with history once; nothing is deleted.

Every answer is asked before anything is written, so a failed request leaves the data as it was.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import session_factory
from app.entities.authority import record
from app.nlp.models import Entity
from app.wikidata.cache import cached_items, store_items
from app.wikidata.client import WikidataClient
from app.wikidata.links import write_identifiers
from app.wikidata.models import EntityExternalId, WikidataItem
from app.wikidata.names import add_names
from app.wikidata.parsing import PageInfo


@dataclass
class RefreshStats:
    checked: int = 0
    changed: int = 0
    redirected: int = 0
    missing: int = 0


@dataclass(frozen=True)
class _Before:
    state: str
    revision: int | None
    claims_fetched: bool
    checked_at: datetime


def _needs_fetch(page: PageInfo, before: _Before | None) -> bool:
    if page.state != "ok" or before is None:
        return True
    return before.state != "ok" or not before.claims_fetched or before.revision != page.revision


async def refresh_roots(
    client: WikidataClient,
    root_ids: Sequence[uuid.UUID],
    settings: Settings,
    *,
    force: bool,
    add_labels: bool = False,
) -> RefreshStats:
    """Refresh the items these roots link to: those due, or all of them with `force`."""
    stats = RefreshStats()
    async with session_factory() as db:
        links = {
            root_id: qid
            for root_id, qid in await db.execute(
                select(EntityExternalId.entity_id, EntityExternalId.value).where(
                    EntityExternalId.entity_id.in_(list(root_ids)),
                    EntityExternalId.scheme == "wikidata",
                )
            )
        }
        cached = await cached_items(db, links.values())
        before = {
            qid: _Before(row.state, row.revision, row.claims_fetched, row.checked_at)
            for qid, row in cached.items()
        }
        cutoff = datetime.now(UTC) - timedelta(days=settings.wikidata_refresh_days)
        due = [
            qid
            for qid in dict.fromkeys(links.values())
            if force
            or qid not in before
            or not before[qid].claims_fetched
            or before[qid].checked_at < cutoff
        ]
        if not due:
            return stats
        # Every request first; nothing is written until all of them have answered.
        pages = await client.info(due)
        fetch = [qid for qid in due if _needs_fetch(pages[qid], before.get(qid))]
        items = await client.full_items(fetch, pages) if fetch else []

        await store_items(db, items)
        unchanged = [qid for qid in due if qid not in fetch]
        if unchanged:
            await db.execute(
                update(WikidataItem)
                .where(WikidataItem.qid.in_(unchanged))
                .values(checked_at=func.now())
            )
        found = {item.qid: item for item in items}
        # The rows loaded above are stale now: read what the cache holds after the writes.
        db.expire_all()
        rows = await cached_items(db, [*due, *(item.qid for item in items)])
        for root_id, qid in links.items():
            if qid not in due:
                continue
            stats.checked += 1
            item = found.get(qid)
            if item is None:
                continue
            old = before.get(qid)
            if item.state == "ok":
                if old is None or old.revision != item.revision:
                    stats.changed += 1
                await write_identifiers(db, root_id, rows[qid])
                if add_labels:
                    root = await db.get(Entity, root_id)
                    if root is not None:
                        await add_names(db, root, rows[qid], [])
            elif item.state == "redirected" and item.redirect_to:
                stats.redirected += 1
                await _follow(db, root_id, qid, item.redirect_to, rows)
            elif item.state == "missing":
                stats.missing += 1
                if old is None or old.state != "missing":
                    record(db, "wikidata_missing", root_id, before={"qid": qid})
        await db.commit()
    return stats


async def _follow(
    db: AsyncSession, root_id: uuid.UUID, old: str, new: str, rows: dict[str, WikidataItem]
) -> None:
    """Move the link to the item `old` became, unless another root holds it (a merge to offer)."""
    holder = await db.scalar(
        select(EntityExternalId.entity_id).where(
            EntityExternalId.scheme == "wikidata", EntityExternalId.value == new
        )
    )
    if holder is not None:
        return
    await db.execute(
        update(EntityExternalId)
        .where(
            EntityExternalId.entity_id == root_id,
            EntityExternalId.scheme == "wikidata",
            EntityExternalId.value == old,
        )
        .values(value=new)
    )
    record(db, "wikidata_redirected", root_id, before={"qid": old}, after={"qid": new})
    target = rows.get(new)
    if target is not None and target.state == "ok":
        await write_identifiers(db, root_id, target)
