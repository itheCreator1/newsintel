import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.entities.models import Entity
from app.entities.relations import expand
from app.entities.resolver import expand_for_search
from app.events.models import EventCluster
from app.feeds.models import Feed
from app.nlp.models import Keyword
from app.nlp.processors import entity_name_keys
from app.search.documents import SCHEMA_VERSION
from app.search.models import SearchIndexTarget
from app.search.query import ParsedQuery, SearchSyntaxError, parse_query


@dataclass(frozen=True)
class SearchCriteria:
    parsed: ParsedQuery
    q: str
    sources: list[str]
    countries: list[str]
    start: date | None
    end: date | None
    content_available: bool | None
    processing: list[str]
    languages: list[str]
    entity_ids: list[str]
    entity_types: list[str]
    keyword_ids: list[str]
    story_countries: list[str]
    mentioned_countries: list[str]
    story_cluster_ids: list[str]
    # entity_ids widened to every name of their roots; what the index is filtered on.
    entity_match_ids: list[str] = field(default_factory=list)
    # Which see-also links entity_match_ids followed: "names", "parts", or none.
    entity_expand: list[str] = field(default_factory=list)
    # Source tags, and the sources that carry any of them; what the index is filtered on.
    source_tags: list[str] = field(default_factory=list)
    tag_sources: list[str] = field(default_factory=list)
    # Events, and the stories they hold now; what the index is filtered on.
    event_ids: list[str] = field(default_factory=list)
    event_clusters: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """No article criterion at all; sort and interval are display preferences, not criteria."""
        return all(value in ("", [], None) for value in self.fingerprint().values())

    def fingerprint(self) -> dict[str, Any]:
        # A key only when set, so every criteria fingerprint made before see-also stays the same.
        expanded = {"entity_expand": self.entity_expand} if self.entity_expand else {}
        tagged = {"source_tags": self.source_tags} if self.source_tags else {}
        events = {"events": self.event_ids} if self.event_ids else {}
        return {
            "q": self.q,
            "sources": self.sources,
            "countries": self.countries,
            "after": str(self.start or ""),
            "before": str(self.end or ""),
            "content": self.content_available,
            "processing": self.processing,
            "languages": self.languages,
            "entities": self.entity_ids,
            "entity_types": self.entity_types,
            "keywords": self.keyword_ids,
            "story_countries": self.story_countries,
            "mentioned_countries": self.mentioned_countries,
            "story_clusters": self.story_cluster_ids,
            **expanded,
            **tagged,
            **events,
        }


async def _resolve_sources(db: AsyncSession, values: list[str]) -> list[str]:
    resolved: set[str] = set()
    names: list[str] = []
    for value in values:
        try:
            resolved.add(str(uuid.UUID(value)))
        except ValueError:
            names.append(value.lower())
    if names:
        resolved.update(
            str(value)
            for value in (
                await db.scalars(select(Feed.id).where(func.lower(Feed.name).in_(names)))
            ).all()
        )
    return sorted(resolved)


async def _tag_sources(db: AsyncSession, tags: list[str]) -> list[str]:
    """Every source, retired ones too, that carries any of the tags."""
    if not tags:
        return []
    rows = await db.scalars(select(Feed.id).where(Feed.tags.has_any(array(tags))))
    return sorted(str(value) for value in rows.all())


async def _event_clusters(db: AsyncSession, events: list[str]) -> list[str]:
    """The stories the events hold now; an event that gains a story later widens a saved search."""
    if not events:
        return []
    rows = await db.scalars(
        select(EventCluster.cluster_id).where(
            EventCluster.event_id.in_([uuid.UUID(value) for value in events])
        )
    )
    return sorted({str(value) for value in rows.all()})


async def _resolve_annotations(
    db: AsyncSession, values: list[str], model: type[Entity] | type[Keyword]
) -> list[str]:
    resolved: set[str] = set()
    names: list[str] = []
    for value in values:
        try:
            resolved.add(str(uuid.UUID(value)))
        except ValueError:
            if model is Entity:
                # The keys extraction stores a name under: "τσιπρα", "hague", "united states".
                names.extend(entity_name_keys(value))
            else:
                names.append(" ".join(value.casefold().split()))
    if names:
        resolved.update(
            str(value)
            for value in (
                await db.scalars(select(model.id).where(model.normalized_text.in_(names)))
            ).all()
        )
    return sorted(resolved)


async def search_criteria(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = "",
    source_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    source_country: Annotated[list[str] | None, Query()] = None,
    after: date | None = None,
    before: date | None = None,
    content_available: bool | None = None,
    processing_status: Annotated[list[str] | None, Query()] = None,
    language: Annotated[list[str] | None, Query()] = None,
    entity_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    entity_type: Annotated[list[str] | None, Query()] = None,
    keyword_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    story_country: Annotated[list[str] | None, Query()] = None,
    mentioned_country: Annotated[list[str] | None, Query()] = None,
    story_cluster_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    entity_expand: Annotated[list[Literal["names", "parts"]] | None, Query()] = None,
    source_tag: Annotated[list[str] | None, Query()] = None,
    event_id: Annotated[list[uuid.UUID] | None, Query()] = None,
) -> SearchCriteria:
    try:
        parsed = parse_query(q)
    except SearchSyntaxError as exc:
        raise HTTPException(422, str(exc)) from exc
    tags = {" ".join(value.split()) for value in source_tag or []} - {""}
    events = sorted({str(value) for value in event_id or []})
    entity_ids = sorted(
        {
            *(str(value) for value in entity_id or []),
            *(await _resolve_annotations(db, parsed.entity_values, Entity)),
        }
    )
    return SearchCriteria(
        parsed=parsed,
        q=q.strip(),
        sources=sorted(
            {
                *(str(value) for value in source_id or []),
                *(await _resolve_sources(db, parsed.source_values)),
            }
        ),
        countries=sorted({value.upper() for value in source_country or []} | set(parsed.countries)),
        start=after or parsed.after,
        end=before or parsed.before,
        content_available=content_available,
        processing=sorted(processing_status or []),
        languages=sorted({value.casefold() for value in language or []} | set(parsed.languages)),
        entity_ids=entity_ids,
        entity_match_ids=await expand_for_search(
            db, await expand(db, entity_ids, set(entity_expand or []))
        ),
        entity_expand=sorted(set(entity_expand or [])),
        source_tags=sorted(tags),
        tag_sources=await _tag_sources(db, sorted(tags)),
        event_ids=events,
        event_clusters=await _event_clusters(db, events),
        entity_types=sorted({value.upper() for value in entity_type or []}),
        keyword_ids=sorted(
            {
                *(str(value) for value in keyword_id or []),
                *(await _resolve_annotations(db, parsed.keyword_values, Keyword)),
            }
        ),
        story_countries=sorted(
            {value.upper() for value in story_country or []} | set(parsed.story_countries)
        ),
        mentioned_countries=sorted(
            {value.upper() for value in mentioned_country or []} | set(parsed.mentioned_countries)
        ),
        story_cluster_ids=sorted(
            {
                *(str(value) for value in story_cluster_id or []),
                *parsed.story_cluster_ids,
            }
        ),
    )


async def live_index(db: AsyncSession) -> SearchIndexTarget | None:
    """The index Search reads: the newest current target (a cutover leaves one)."""
    target: SearchIndexTarget | None = await db.scalar(
        select(SearchIndexTarget)
        .where(SearchIndexTarget.role == "current")
        .order_by(SearchIndexTarget.created_at.desc())
        .limit(1)
    )
    return target


REBUILD_COMMAND = "python -m app.cli rebuild-search in the api container"


def rebuild_needed(target: SearchIndexTarget | None) -> bool:
    """An older version built the live index: its fields are not the ones queries ask for."""
    return target is not None and target.schema_version < SCHEMA_VERSION


async def current_search_target(db: AsyncSession) -> str:
    """The index to query, or 503 until a rebuild builds one this version can read."""
    target = await live_index(db)
    if target is None or rebuild_needed(target):
        raise HTTPException(
            503,
            {
                "code": "search_upgrade_required",
                "message": (
                    f"There is no search index yet. Build it with {REBUILD_COMMAND}."
                    if target is None
                    else "The search index was built by an older version. "
                    f"Rebuild it with {REBUILD_COMMAND}."
                ),
            },
        )
    return target.index_name


TEXT_FIELDS = ["title^3", "descriptions", "body", "entity_text", "keyword_text"]


def _in_every_language(field: str) -> list[str]:
    """The field and its Greek and English analyses (documents.py), with the field's boost."""
    name, _, boost = field.partition("^")
    suffix = f"^{boost}" if boost else ""
    return [f"{name}{language}{suffix}" for language in ("", ".el", ".en")]


# A word also matches its Greek and English forms; a quoted phrase stays exact.
WORD_FIELDS = [variant for field in TEXT_FIELDS for variant in _in_every_language(field)]


def build_query(criteria: SearchCriteria) -> dict[str, Any]:  # noqa: C901
    must: list[dict[str, Any]] = [
        {
            "multi_match": {
                "query": term,
                "fields": WORD_FIELDS,
                "type": "most_fields",
                "operator": "and",
            }
        }
        for term in criteria.parsed.terms
    ]
    must.extend(
        {"multi_match": {"query": phrase, "fields": TEXT_FIELDS, "type": "phrase"}}
        for phrase in criteria.parsed.phrases
    )
    filters: list[dict[str, Any]] = []
    provenance_must: list[dict[str, Any]] = []
    if criteria.sources:
        provenance_must.append({"terms": {"provenance.source_id": criteria.sources}})
    if criteria.countries:
        provenance_must.append({"terms": {"provenance.source_country": criteria.countries}})
    if criteria.source_tags:
        # An empty list matches nothing: a tag no source carries leaves no article.
        provenance_must.append({"terms": {"provenance.source_id": criteria.tag_sources}})
    if provenance_must:
        filters.append(
            {"nested": {"path": "provenance", "query": {"bool": {"must": provenance_must}}}}
        )
    if criteria.start or criteria.end:
        bounds: dict[str, str] = {}
        if criteria.start:
            bounds["gte"] = datetime.combine(criteria.start, time.min, UTC).isoformat()
        if criteria.end:
            bounds["lt"] = datetime.combine(criteria.end, time.min, UTC).isoformat()
        filters.append({"range": {"effective_date": bounds}})
    if criteria.content_available is not None:
        filters.append({"term": {"content_available": criteria.content_available}})
    if criteria.processing:
        filters.append({"terms": {"processing_status": criteria.processing}})
    if criteria.languages:
        filters.append({"terms": {"detected_language": criteria.languages}})
    entity_must: list[dict[str, Any]] = []
    if criteria.entity_ids or criteria.parsed.entity_values:
        # Criteria that tests build by hand leave the match ids out.
        entity_must.append(
            {"terms": {"entities.id": criteria.entity_match_ids or criteria.entity_ids}}
        )
    if criteria.entity_types:
        entity_must.append({"terms": {"entities.type": criteria.entity_types}})
    if entity_must:
        filters.append({"nested": {"path": "entities", "query": {"bool": {"must": entity_must}}}})
    if criteria.keyword_ids or criteria.parsed.keyword_values:
        filters.append({"terms": {"keyword_ids": criteria.keyword_ids}})
    if criteria.story_countries:
        filters.append({"terms": {"primary_story_country": criteria.story_countries}})
    if criteria.mentioned_countries:
        filters.append({"terms": {"mentioned_countries": criteria.mentioned_countries}})
    if criteria.story_cluster_ids:
        filters.append({"terms": {"story_cluster_id": criteria.story_cluster_ids}})
    if criteria.event_ids:
        # An empty list matches nothing: an event with no stories left leaves no article.
        filters.append({"terms": {"story_cluster_id": criteria.event_clusters}})
    return {"bool": {"must": must or [{"match_all": {}}], "filter": filters}}
