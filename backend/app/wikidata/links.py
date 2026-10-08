"""Link an authority root to a Wikidata item, or take the link back. Only the user does this.

A link is the user's decision (MARC 024 $2 wikidata) and lives in `entity_external_ids`; the
VIAF, ISNI and LCNAF numbers beside it are read off the item. Nothing here reaches Wikidata: a
link to an item the cache holds without its claims queues a refresh run, which fetches it.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.authority import AuthorityError, record
from app.nlp.models import Entity
from app.wikidata.cache import cached_items
from app.wikidata.models import EntityExternalId, WikidataCandidate, WikidataItem, WikidataRun
from app.wikidata.names import Name, NameState, add_names, item_names, name_states
from app.wikidata.parsing import is_qid

ID_SCHEMES = ("viaf", "isni", "lcnaf")


class LinkError(AuthorityError):
    """A refusal; `holder` is the root that already has the QID, for a "merge them?" button."""

    def __init__(self, status_code: int, detail: str, holder: Entity | None = None) -> None:
        super().__init__(status_code, detail)
        self.holder = holder

    def body(self) -> dict[str, Any]:
        return {
            "message": self.detail,
            "entity_id": str(self.holder.id) if self.holder else None,
            "display_name": self.holder.name if self.holder else None,
        }


@dataclass(frozen=True)
class LinkState:
    root: Entity
    qid: str | None
    identifiers: dict[str, str]
    item: WikidataItem | None
    fetch_pending: bool
    names: list[NameState]
    # The root that holds the item this one was merged into, when it is not this root.
    redirect_holder: Entity | None = None


async def _root(db: AsyncSession, entity_id: uuid.UUID, *, lock: bool = False) -> Entity:
    entity = await db.get(Entity, entity_id)
    if entity is None:
        raise LinkError(404, "Entity not found")
    root_id = entity.authority_id or entity.id
    query = select(Entity).where(Entity.id == root_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    root = await db.scalar(query)
    assert root is not None
    return root


async def _row(db: AsyncSession, entity_id: uuid.UUID) -> EntityExternalId | None:
    row: EntityExternalId | None = await db.scalar(
        select(EntityExternalId).where(
            EntityExternalId.entity_id == entity_id, EntityExternalId.scheme == "wikidata"
        )
    )
    return row


async def write_identifiers(db: AsyncSession, root_id: uuid.UUID, item: WikidataItem) -> None:
    """Replace the identifiers read off the item; the user's own rows are never touched."""
    await db.execute(
        delete(EntityExternalId).where(
            EntityExternalId.entity_id == root_id, EntityExternalId.source == "wikidata"
        )
    )
    for scheme in ID_SCHEMES:
        value = (item.ids or {}).get(scheme)
        if value:
            db.add(
                EntityExternalId(entity_id=root_id, scheme=scheme, value=value, source="wikidata")
            )
    await db.flush()


async def queue_fetch(
    db: AsyncSession, root_id: uuid.UUID, *, add_labels: bool = False
) -> WikidataRun:
    """Ask for the linked item in full; one unfinished run per root is enough.

    `add_labels`: the item was not cached when linked, so its labels come with the fetch.
    """
    run: WikidataRun | None = await db.scalar(
        select(WikidataRun).where(
            WikidataRun.entity_id == root_id,
            WikidataRun.kind == "refresh",
            WikidataRun.status.in_(("queued", "running")),
        )
    )
    if run is None:
        run = WikidataRun(kind="refresh", entity_id=root_id, force=True, add_labels=add_labels)
        db.add(run)
    elif add_labels:
        run.add_labels = True
    await db.flush()
    await db.refresh(run)
    return run


def _offered(item: WikidataItem | None, chosen: list[Name]) -> list[Name]:
    """The chosen names, each checked against the item's own labels and aliases."""
    if not chosen:
        return []
    if item is None:
        raise LinkError(422, "The item has not been fetched yet; add its names once it is")
    offered = {(name.language, name.text): name for name in item_names(item)}
    picked = []
    for name in chosen:
        found = offered.get((name.language, name.text))
        if found is None:
            raise LinkError(422, f"{name.text!r} ({name.language}) is not a name of {item.qid}")
        picked.append(found)
    return picked


async def link(
    db: AsyncSession, entity_id: uuid.UUID, qid: str, aliases: list[Name] | None = None
) -> LinkState:
    """Link the entity's root to `qid`, following a redirect the cache knows about.

    The item's labels become variants of the root, with the aliases the user ticked.
    """
    if not is_qid(qid):
        raise LinkError(422, f"Not a Wikidata item id: {qid!r}")
    root = await _root(db, entity_id, lock=True)
    if root.ambiguous:
        raise LinkError(409, "An ambiguous name stands for several people: it has no one item")
    item = (await cached_items(db, [qid])).get(qid)
    if item is not None and item.state == "redirected" and item.redirect_to:
        qid = item.redirect_to
        item = (await cached_items(db, [qid])).get(qid)
    if item is not None and item.state == "missing":
        raise LinkError(409, f"Wikidata no longer has the item {qid}")
    chosen = _offered(item, aliases or [])
    current = await _row(db, root.id)
    if current is not None:
        if current.value == qid:
            if item is not None and chosen:
                await add_names(db, root, item, chosen)
            return await state(db, root.id)
        raise LinkError(409, f"This entity is already linked to {current.value}; unlink it first")
    holder_id = await db.scalar(
        select(EntityExternalId.entity_id).where(
            EntityExternalId.scheme == "wikidata", EntityExternalId.value == qid
        )
    )
    if holder_id is not None:
        holder = await db.get(Entity, holder_id)
        raise LinkError(
            409,
            f"{qid} is already linked to {holder.name if holder else 'another entity'};"
            " merge the two entities instead",
            holder,
        )
    db.add(EntityExternalId(entity_id=root.id, scheme="wikidata", value=qid, source="user"))
    await db.flush()
    if item is not None and item.claims_fetched:
        await write_identifiers(db, root.id, item)
    else:
        await queue_fetch(db, root.id, add_labels=item is None)
    # The choice is made: the other suggestions for this root are spent.
    await db.execute(delete(WikidataCandidate).where(WikidataCandidate.entity_id == root.id))
    record(db, "wikidata_linked", root.id, after={"qid": qid})
    if item is not None:
        await add_names(db, root, item, chosen)
    await db.flush()
    return await state(db, root.id)


async def add_item_names(db: AsyncSession, entity_id: uuid.UUID, names: list[Name]) -> LinkState:
    """Add more of the linked item's names (an alias ticked later, a label it gained)."""
    root = await _root(db, entity_id, lock=True)
    current = await _row(db, root.id)
    if current is None:
        raise LinkError(409, "This entity is not linked to Wikidata")
    item = (await cached_items(db, [current.value])).get(current.value)
    chosen = _offered(item, names)
    if item is not None:
        await add_names(db, root, item, chosen)
    return await state(db, root.id)


async def unlink(db: AsyncSession, entity_id: uuid.UUID) -> None:
    """Remove the link and the identifiers read off the item; names it brought stay."""
    root = await _root(db, entity_id, lock=True)
    current = await _row(db, root.id)
    if current is None:
        raise LinkError(409, "This entity is not linked to Wikidata")
    qid = current.value
    await db.execute(
        delete(EntityExternalId).where(
            EntityExternalId.entity_id == root.id,
            (EntityExternalId.scheme == "wikidata") | (EntityExternalId.source == "wikidata"),
        )
    )
    record(db, "wikidata_unlinked", root.id, before={"qid": qid})
    await db.flush()


async def state(db: AsyncSession, entity_id: uuid.UUID) -> LinkState:
    """What the entity page shows: the link, the identifiers and the cached item."""
    root = await _root(db, entity_id)
    rows = list(
        await db.scalars(select(EntityExternalId).where(EntityExternalId.entity_id == root.id))
    )
    qid = next((row.value for row in rows if row.scheme == "wikidata"), None)
    item = (await cached_items(db, [qid])).get(qid) if qid else None
    pending = bool(
        await db.scalar(
            select(
                exists().where(
                    WikidataRun.entity_id == root.id,
                    WikidataRun.kind == "refresh",
                    WikidataRun.status.in_(("queued", "running")),
                )
            )
        )
    )
    holder = None
    if item is not None and item.state == "redirected" and item.redirect_to:
        holder_id = await db.scalar(
            select(EntityExternalId.entity_id).where(
                EntityExternalId.scheme == "wikidata",
                EntityExternalId.value == item.redirect_to,
            )
        )
        if holder_id is not None and holder_id != root.id:
            holder = await db.get(Entity, holder_id)
    return LinkState(
        root=root,
        qid=qid,
        identifiers={row.scheme: row.value for row in rows if row.scheme != "wikidata"},
        item=item,
        fetch_pending=pending,
        names=await name_states(db, root, item) if item is not None and item.state == "ok" else [],
        redirect_holder=holder,
    )
