"""Pairs of names that may be the same entity, for the user to approve (merge) or reject.

Nothing here changes data. A pair is two roots of the same language and type (a GPE and a
LOCATION count as one type), never two names already joined, never a pair the user said is
different, and never a name marked ambiguous. Each rule that matches adds a reason; the score is
the strongest rule's weight plus a little for every article both names appear in.
"""

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from sqlalchemy import ColumnElement, case, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.entities.authority import PLACE_TYPES
from app.nlp.models import ArticleEntity, Entity, EntityDistinct

__all__ = ["Suggestion", "suggest"]

WEIGHTS = {"acronym": 0.8, "initials": 0.8, "surname": 0.6, "similar spelling": 0.6}
SIMILARITY = 0.6
# Each shared article adds this much, up to SHARED_CAP: context backs a rule, it is not one.
SHARED_STEP = 0.05
SHARED_CAP = 0.2
# Trigram pairs read per language; the closest come first.
TRIGRAM_PAIRS = 5000
# Words an acronym usually skips ("Bank of England" is BoE or BE).
CONNECTORS = frozenset(
    {
        "of",
        "the",
        "and",
        "for",
        "de",
        "la",
        "le",
        "du",
        "des",
        "del",
        "der",
        "die",
        "das",
        "und",
        "και",
        "του",
        "της",
        "των",
    }
)
# Bound parameters per statement stay far below asyncpg's limit.
CHUNK = 1000
_WORD = re.compile(r"\w+")


@dataclass
class Suggestion:
    root: Entity
    variant: Entity
    score: float
    reasons: list[str]
    shared_articles: int
    root_articles: int
    variant_articles: int


@dataclass(frozen=True)
class _Name:
    id: uuid.UUID
    language: str
    group: str
    text: str
    words: tuple[str, ...]


@dataclass
class _Pair:
    reasons: set[str] = field(default_factory=set)


def _group(entity_type: str) -> str:
    return "PLACE" if entity_type in PLACE_TYPES else entity_type


def _initials(words: Iterable[str]) -> str:
    return "".join(word[0] for word in words)


def _abbreviates(short: tuple[str, ...], full: tuple[str, ...]) -> bool:
    """True when `short` is `full` with one or more words cut to their first letter."""
    cut = False
    for brief, word in zip(short, full, strict=True):
        if brief == word:
            continue
        if len(brief) != 1 or len(word) == 1 or word[0] != brief:
            return False
        cut = True
    return cut


def _rule_pairs(names: Sequence[_Name]) -> dict[tuple[uuid.UUID, uuid.UUID], _Pair]:
    pairs: dict[tuple[uuid.UUID, uuid.UUID], _Pair] = defaultdict(_Pair)

    def add(a: _Name, b: _Name, reason: str) -> None:
        pairs[(min(a.id, b.id), max(a.id, b.id))].reasons.add(reason)

    single: dict[tuple[str, str], list[_Name]] = defaultdict(list)
    by_shape: dict[tuple[str, str, str], list[_Name]] = defaultdict(list)
    by_last: dict[tuple[str, str], list[_Name]] = defaultdict(list)
    for name in names:
        if len(name.words) == 1:
            single[(name.group, name.words[0])].append(name)
        else:
            by_shape[(name.group, _initials(name.words), name.words[-1])].append(name)
            by_last[(name.group, name.words[-1])].append(name)

    for name in names:
        if len(name.words) < 2:
            continue
        # Acronym: "european union" and "eu"; connectors may be skipped or kept.
        kept = [word for word in name.words if word not in CONNECTORS]
        for acronym in {_initials(name.words), _initials(kept)}:
            if len(acronym) >= 2:
                for other in single.get((name.group, acronym), []):
                    add(name, other, "acronym")
        # Surname: "angela merkel" and "merkel", for people only.
        if name.group == "PERSON" and len(name.words[-1]) >= 3:
            for other in single.get((name.group, name.words[-1]), []):
                add(name, other, "surname")

    # Initials: "donald trump" and "d. trump" share the shape and the last word.
    for bucket in by_shape.values():
        for a, b in combinations(bucket, 2):
            if len(a.words) == len(b.words) and (
                _abbreviates(a.words, b.words) or _abbreviates(b.words, a.words)
            ):
                add(a, b, "initials")
    return pairs


def _chunks[T](items: Sequence[T]) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


async def _roots(db: AsyncSession, language: str | None) -> list[_Name]:
    query = select(Entity.id, Entity.language, Entity.entity_type, Entity.normalized_text).where(
        Entity.authority_id.is_(None), Entity.ambiguous.is_(False)
    )
    if language is not None:
        query = query.where(Entity.language == language)
    names = []
    for row in await db.execute(query):
        words = tuple(_WORD.findall(row.normalized_text))
        if words:
            names.append(
                _Name(row.id, row.language, _group(row.entity_type), row.normalized_text, words)
            )
    return names


async def _similar(db: AsyncSession, language: str | None) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Roots spelled alike, by pg_trgm on the normalized text (its GIN index serves `%`)."""
    await db.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {SIMILARITY}"))
    a, b = aliased(Entity), aliased(Entity)

    def group(entity: type[Entity]) -> ColumnElement[str]:
        return case((entity.entity_type.in_(PLACE_TYPES), "PLACE"), else_=entity.entity_type)

    query = (
        select(a.id, b.id)
        .join(b, a.normalized_text.op("%")(b.normalized_text))
        .where(
            a.id < b.id,
            a.language == b.language,
            group(a) == group(b),
            a.authority_id.is_(None),
            b.authority_id.is_(None),
            a.ambiguous.is_(False),
            b.ambiguous.is_(False),
        )
        .order_by(func.similarity(a.normalized_text, b.normalized_text).desc(), a.id, b.id)
        .limit(TRIGRAM_PAIRS)
    )
    if language is not None:
        query = query.where(a.language == language)
    return [(row[0], row[1]) for row in await db.execute(query)]


async def _rejected(
    db: AsyncSession, keys: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    found: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for chunk in _chunks(keys):
        rows = await db.execute(
            select(EntityDistinct.a_id, EntityDistinct.b_id).where(
                tuple_(EntityDistinct.a_id, EntityDistinct.b_id).in_(chunk)
            )
        )
        found.update((row[0], row[1]) for row in rows)
    return found


async def _article_counts(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    counts: dict[uuid.UUID, int] = {}
    for chunk in _chunks(ids):
        rows = await db.execute(
            select(ArticleEntity.entity_id, func.count(func.distinct(ArticleEntity.article_id)))
            .where(ArticleEntity.is_current, ArticleEntity.entity_id.in_(chunk))
            .group_by(ArticleEntity.entity_id)
        )
        counts.update((row[0], row[1]) for row in rows)
    return counts


async def _shared(
    db: AsyncSession, keys: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    x, y = aliased(ArticleEntity), aliased(ArticleEntity)
    shared: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
    for chunk in _chunks(keys):
        rows = await db.execute(
            select(x.entity_id, y.entity_id, func.count(func.distinct(x.article_id)))
            .join(y, y.article_id == x.article_id)
            .where(x.is_current, y.is_current, tuple_(x.entity_id, y.entity_id).in_(chunk))
            .group_by(x.entity_id, y.entity_id)
        )
        shared.update(((row[0], row[1]), row[2]) for row in rows)
    return shared


async def suggest(
    db: AsyncSession, *, language: str | None = None, limit: int = 50
) -> list[Suggestion]:
    """The likeliest duplicate pairs first; the name with more articles is the one to keep."""
    names = await _roots(db, language)
    by_language: dict[str, list[_Name]] = defaultdict(list)
    for name in names:
        by_language[name.language].append(name)
    pairs: dict[tuple[uuid.UUID, uuid.UUID], _Pair] = {}
    for group in by_language.values():
        pairs.update(_rule_pairs(group))
    for key in await _similar(db, language):
        pairs.setdefault(key, _Pair()).reasons.add("similar spelling")

    keys = sorted(set(pairs) - await _rejected(db, sorted(pairs)))
    if not keys:
        return []
    ids = sorted({item for key in keys for item in key})
    articles = await _article_counts(db, ids)
    shared = await _shared(db, keys)
    texts = {name.id: name.text for name in names}

    ranked = []
    for key in keys:
        together = shared.get(key, 0)
        reasons = sorted(pairs[key].reasons, key=lambda reason: -WEIGHTS[reason])
        if together:
            reasons.append("shared articles")
        score = max(WEIGHTS[reason] for reason in pairs[key].reasons)
        score = min(1.0, score + min(SHARED_CAP, SHARED_STEP * together))
        # Keep the name with more articles; on a tie the fuller one, then the earlier one.
        root, variant = sorted(
            key, key=lambda item: (-articles.get(item, 0), -len(texts[item]), texts[item])
        )
        ranked.append((round(score, 4), together, texts[root], root, variant, reasons))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    ranked = ranked[:limit]

    loaded = {
        entity.id: entity
        for entity in await db.scalars(
            select(Entity).where(Entity.id.in_({key for item in ranked for key in item[3:5]}))
        )
    }
    return [
        Suggestion(
            root=loaded[root],
            variant=loaded[variant],
            score=score,
            reasons=reasons,
            shared_articles=together,
            root_articles=articles.get(root, 0),
            variant_articles=articles.get(variant, 0),
        )
        for score, together, _, root, variant, reasons in ranked
    ]
