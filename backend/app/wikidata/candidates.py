"""Suggest Wikidata items for authority roots; only the user links one (decided 2026-10-08).

A root is searched by its name, and by one name in each other language, through the search
cache (a search waits wikidata_search_cache_days, an empty one too). The items found are fetched
light (no claims) in one batch, and scored with stated reasons, as "Maybe the same?" does:

- the exact label in the root's language 0.6, else an exact alias 0.4;
- a label in another language that is already one of the root's names +0.2;
- the type fits +0.2, does not fit -0.4 (P31 asked only for exact labels, the top three);
- how known the item is, up to +0.1 from its sitelinks (two "Georgia"s).

An item with none of the root's names is no candidate. A root's one exact label of the right
type is marked `exact`: the "approve all exact" button links those, and only those.
"""

import math
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import session_factory
from app.nlp.models import ArticleEntity, Entity
from app.wikidata.cache import cached_items, store_items
from app.wikidata.classes import type_matches
from app.wikidata.client import WikidataClient
from app.wikidata.models import EntityExternalId, WikidataCandidate, WikidataItem, WikidataSearch
from app.wikidata.names import name_key
from app.wikidata.parsing import INSTANCE_OF

WEIGHTS = {"label": 0.6, "alias": 0.4, "variant": 0.2, "type_matches": 0.2, "type_differs": -0.4}
SITELINK_WEIGHT = 0.1
# About the sitelinks of a country: an item this known gets the whole bonus.
SITELINK_SCALE = 400
# Exact labels whose type is asked, best first: a fourth "Georgia" is not worth a request.
CLOSE_CALLS = 3


class Named(Protocol):
    qid: str
    labels: dict[str, str]
    aliases: dict[str, list[str]]
    sitelinks: int


@dataclass(frozen=True)
class Scored:
    qid: str
    score: float
    reasons: list[str]
    label_match: bool
    type_match: bool | None


def score_item(
    item: Named,
    *,
    language: str,
    entity_type: str,
    names: dict[str, set[str]],
    type_match: bool | None,
) -> Scored | None:
    """The item scored for a root of this type and language whose names (as NER keys) are given."""

    def key(lang: str, text: str) -> str:
        return name_key(entity_type, lang, text)

    own = names.get(language, set())
    reasons: list[str] = []
    label = (item.labels or {}).get(language)
    label_match = label is not None and key(language, label) in own
    if label_match:
        reasons.append(f"label:{language}")
    elif any(key(language, alias) in own for alias in (item.aliases or {}).get(language, [])):
        reasons.append(f"alias:{language}")
    for other, text in sorted((item.labels or {}).items()):
        if other != language and key(other, text) in names.get(other, set()):
            reasons.append(f"variant:{other}")
            break
    if not reasons:
        return None
    if type_match is not None:
        reasons.append("type_matches" if type_match else "type_differs")
    score = sum(WEIGHTS[reason.split(":")[0]] for reason in reasons)
    if item.sitelinks:
        reasons.append(f"sitelinks:{item.sitelinks}")
        score += SITELINK_WEIGHT * min(1.0, math.log1p(item.sitelinks) / math.log1p(SITELINK_SCALE))
    return Scored(item.qid, round(score, 3), reasons, label_match, type_match)


def exact_choice(scored: Sequence[Scored]) -> str | None:
    """The one item with the root's exact label and the right type; None when it is a choice."""
    labelled = [found for found in scored if found.label_match]
    if len(labelled) == 1 and labelled[0].type_match is True:
        return labelled[0].qid
    return None


@dataclass
class _Root:
    entity: Entity
    names: dict[str, set[str]]
    searches: list[tuple[str, str]]


async def _roots(
    db: AsyncSession, root_ids: Sequence[uuid.UUID], languages: Sequence[str]
) -> list[_Root]:
    found: list[_Root] = []
    for root_id in root_ids:
        root = await db.get(Entity, root_id)
        if root is None or root.authority_id is not None or root.ambiguous:
            continue
        variants = list(await db.scalars(select(Entity).where(Entity.authority_id == root.id)))
        names: dict[str, set[str]] = defaultdict(set)
        for entity in (root, *variants):
            names[entity.language].add(entity.normalized_text)
        searches = [(root.language, root.name)] if root.language in languages else []
        # One name in each other language: the spelling most articles used.
        uses: dict[uuid.UUID | None, int] = {
            entity_id: count
            for entity_id, count in (
                await db.execute(
                    select(ArticleEntity.observed_entity_id, func.count())
                    .where(
                        ArticleEntity.entity_id == root.id,
                        ArticleEntity.observed_entity_id.is_not(None),
                    )
                    .group_by(ArticleEntity.observed_entity_id)
                )
            ).all()
        }
        for language in languages:
            if language == root.language:
                continue
            spelled = [variant for variant in variants if variant.language == language]
            if spelled:
                best = max(spelled, key=lambda variant: (uses.get(variant.id, 0), variant.name))
                searches.append((language, best.name))
        found.append(_Root(root, dict(names), searches))
    return found


async def _search(
    db: AsyncSession, client: WikidataClient, language: str, text: str, settings: Settings
) -> list[str]:
    text = " ".join(text.split())
    fresh = datetime.now(UTC) - timedelta(days=settings.wikidata_search_cache_days)
    cached = await db.scalar(
        select(WikidataSearch).where(
            WikidataSearch.language == language,
            WikidataSearch.text == text,
            WikidataSearch.fetched_at >= fresh,
        )
    )
    if cached is not None:
        return list(cached.qids)
    qids = await client.search(text, language)
    statement = insert(WikidataSearch).values(language=language, text=text, qids=qids)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["language", "text"],
            set_={"qids": statement.excluded.qids, "fetched_at": func.now()},
        )
    )
    # Kept at once: a pause on the next request must not cost this search again.
    await db.commit()
    return qids


async def _instance_of(
    db: AsyncSession, client: WikidataClient, item: WikidataItem, asked: dict[str, list[str]]
) -> list[str]:
    """The item's classes: cached, or asked with wbgetclaims and kept on the cached item."""
    if item.instance_of or item.claims_fetched:
        return list(item.instance_of)
    if item.qid not in asked:
        asked[item.qid] = await client.claim_items(item.qid, INSTANCE_OF)
        await db.execute(
            update(WikidataItem)
            .where(WikidataItem.qid == item.qid, WikidataItem.claims_fetched.is_(False))
            .values(instance_of=asked[item.qid])
        )
        await db.commit()
    return asked[item.qid]


async def _write(db: AsyncSession, root_id: uuid.UUID, scored: list[Scored]) -> None:
    """Replace the root's open candidates; a dismissed one stays dismissed and unchanged."""
    exact = exact_choice(scored)
    await db.execute(
        delete(WikidataCandidate).where(
            WikidataCandidate.entity_id == root_id,
            WikidataCandidate.dismissed.is_(False),
            WikidataCandidate.qid.not_in([found.qid for found in scored] or [""]),
        )
    )
    for found in scored:
        statement = insert(WikidataCandidate).values(
            entity_id=root_id,
            qid=found.qid,
            score=found.score,
            reasons=found.reasons,
            exact=found.qid == exact,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["entity_id", "qid"],
                set_={
                    "score": statement.excluded.score,
                    "reasons": statement.excluded.reasons,
                    "exact": statement.excluded.exact,
                },
                where=WikidataCandidate.dismissed.is_(False),
            )
        )
    await db.commit()


async def find_candidates(
    client: WikidataClient, root_ids: Sequence[uuid.UUID], settings: Settings
) -> int:
    """Search, fetch and score candidates for these roots; returns the roots done.

    Every answer is committed as it comes, so a pause or the budget stopping this midway costs
    no request twice. The exceptions of the client (pause, budget, unavailable) go to the caller.
    """
    languages = client.languages
    async with session_factory() as db:
        roots = await _roots(db, root_ids, languages)
        found: dict[uuid.UUID, list[str]] = {}
        for root in roots:
            qids: list[str] = []
            for language, text in root.searches:
                qids.extend(
                    qid
                    for qid in await _search(db, client, language, text, settings)
                    if qid not in qids
                )
            found[root.entity.id] = qids
        wanted = {qid for qids in found.values() for qid in qids}
        held: dict[str, uuid.UUID] = {
            qid: entity_id
            for qid, entity_id in (
                await db.execute(
                    select(EntityExternalId.value, EntityExternalId.entity_id).where(
                        EntityExternalId.scheme == "wikidata",
                        EntityExternalId.value.in_(sorted(wanted)),
                    )
                )
            ).all()
        }
        dismissed = set(
            (
                await db.execute(
                    select(WikidataCandidate.entity_id, WikidataCandidate.qid).where(
                        WikidataCandidate.entity_id.in_(list(found)),
                        WikidataCandidate.dismissed.is_(True),
                    )
                )
            ).all()
        )
        for root_id, qids in found.items():
            found[root_id] = [
                qid
                for qid in qids
                if held.get(qid, root_id) == root_id and (root_id, qid) not in dismissed
            ]
        ordered = list(dict.fromkeys(qid for qids in found.values() for qid in qids))
        cached = await cached_items(db, ordered)
        missing = [qid for qid in ordered if qid not in cached]
        if missing:
            await store_items(db, await client.items(missing))
            await db.commit()
            cached = await cached_items(db, ordered)
        asked: dict[str, list[str]] = {}
        for root in roots:
            await _score_root(db, client, root, found[root.entity.id], cached, asked, settings)
    return len(roots)


async def _score_root(
    db: AsyncSession,
    client: WikidataClient,
    root: _Root,
    qids: list[str],
    cached: dict[str, WikidataItem],
    asked: dict[str, list[str]],
    settings: Settings,
) -> None:
    entity = root.entity
    items = [cached[qid] for qid in qids if qid in cached and cached[qid].state == "ok"]

    def scored_with(types: dict[str, bool | None]) -> list[Scored]:
        found = (
            score_item(
                item,
                language=entity.language,
                entity_type=entity.entity_type,
                names=root.names,
                type_match=types.get(item.qid),
            )
            for item in items
        )
        return sorted(
            (value for value in found if value is not None),
            key=lambda value: (-value.score, value.qid),
        )

    first = scored_with({})
    types: dict[str, bool | None] = {}
    for close in [value for value in first if value.label_match][:CLOSE_CALLS]:
        classes = await _instance_of(db, client, cached[close.qid], asked)
        types[close.qid] = await type_matches(db, client, entity.entity_type, classes, settings)
    await _write(db, entity.id, scored_with(types))


async def dismiss(db: AsyncSession, entity_id: uuid.UUID, qid: str) -> bool:
    """Mark a candidate as not this one; False when the root has no such candidate."""
    entity = await db.get(Entity, entity_id)
    if entity is None:
        return False
    result = await db.execute(
        update(WikidataCandidate)
        .where(
            WikidataCandidate.entity_id == (entity.authority_id or entity.id),
            WikidataCandidate.qid == qid,
        )
        .values(dismissed=True, exact=False)
    )
    return bool(getattr(result, "rowcount", 0))


async def for_root(
    db: AsyncSession, root_id: uuid.UUID
) -> list[tuple[WikidataCandidate, WikidataItem | None]]:
    """The root's open candidates, best first, with their cached items."""
    rows = list(
        await db.scalars(
            select(WikidataCandidate)
            .where(WikidataCandidate.entity_id == root_id, WikidataCandidate.dismissed.is_(False))
            .order_by(WikidataCandidate.score.desc(), WikidataCandidate.qid)
        )
    )
    items = await cached_items(db, [row.qid for row in rows])
    return [(row, items.get(row.qid)) for row in rows]


def label_for(item: WikidataItem | None, language: str) -> str | None:
    """The item's label in the root's language, else in any of ours."""
    if item is None:
        return None
    labels = item.labels or {}
    return labels.get(language) or next(iter(labels.values()), None)


def description_for(item: WikidataItem | None, language: str) -> str | None:
    if item is None:
        return None
    descriptions = item.descriptions or {}
    return descriptions.get(language) or next(iter(descriptions.values()), None)


async def review_queue(
    db: AsyncSession,
    *,
    min_score: float,
    limit: int,
    after: tuple[float, uuid.UUID, str] | None,
) -> tuple[
    list[tuple[WikidataCandidate, Entity, WikidataItem | None]], tuple[float, uuid.UUID, str] | None
]:
    """Open candidates of unlinked roots, best first, for the Authority file's review tab."""
    linked = select(EntityExternalId.entity_id).where(EntityExternalId.scheme == "wikidata")
    query: Any = (
        select(WikidataCandidate, Entity)
        .join(Entity, Entity.id == WikidataCandidate.entity_id)
        .where(
            WikidataCandidate.dismissed.is_(False),
            WikidataCandidate.score >= min_score,
            WikidataCandidate.entity_id.not_in(linked),
        )
    )
    if after is not None:
        score, entity_id, qid = after
        query = query.where(
            (WikidataCandidate.score < score)
            | (
                (WikidataCandidate.score == score)
                & (
                    (WikidataCandidate.entity_id > entity_id)
                    | ((WikidataCandidate.entity_id == entity_id) & (WikidataCandidate.qid > qid))
                )
            )
        )
    rows = list(
        await db.execute(
            query.order_by(
                WikidataCandidate.score.desc(), WikidataCandidate.entity_id, WikidataCandidate.qid
            ).limit(limit + 1)
        )
    )
    page = [(row[0], row[1]) for row in rows[:limit]]
    items = await cached_items(db, [candidate.qid for candidate, _ in page])
    following = None
    if len(rows) > limit:
        last = page[-1][0]
        following = (last.score, last.entity_id, last.qid)
    return [(candidate, entity, items.get(candidate.qid)) for candidate, entity in page], following


async def exact_candidates(db: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    linked = select(EntityExternalId.entity_id).where(EntityExternalId.scheme == "wikidata")
    rows = await db.execute(
        select(WikidataCandidate.entity_id, WikidataCandidate.qid)
        .where(
            WikidataCandidate.exact.is_(True),
            WikidataCandidate.dismissed.is_(False),
            WikidataCandidate.entity_id.not_in(linked),
        )
        .order_by(WikidataCandidate.entity_id)
    )
    return [(entity_id, qid) for entity_id, qid in rows]
