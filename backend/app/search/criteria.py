import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.feeds.models import Feed
from app.nlp.models import Entity, Keyword
from app.search.models import SearchIndexTarget
from app.search.query import ParsedQuery, SearchSyntaxError, parse_query
from app.search.rebuild import ALIAS


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

    @property
    def annotation_search(self) -> bool:
        return bool(
            self.languages
            or self.entity_ids
            or self.entity_types
            or self.keyword_ids
            or self.story_countries
            or self.mentioned_countries
            or self.parsed.entity_values
            or self.parsed.keyword_values
        )

    def fingerprint(self) -> dict[str, Any]:
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


async def _resolve_annotations(
    db: AsyncSession, values: list[str], model: type[Entity] | type[Keyword]
) -> list[str]:
    resolved: set[str] = set()
    names: list[str] = []
    for value in values:
        try:
            resolved.add(str(uuid.UUID(value)))
        except ValueError:
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
) -> SearchCriteria:
    try:
        parsed = parse_query(q)
    except SearchSyntaxError as exc:
        raise HTTPException(422, str(exc)) from exc
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
        entity_ids=sorted(
            {
                *(str(value) for value in entity_id or []),
                *(await _resolve_annotations(db, parsed.entity_values, Entity)),
            }
        ),
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
    )


async def current_search_target(db: AsyncSession, criteria: SearchCriteria) -> tuple[str, int]:
    target = await db.scalar(
        select(SearchIndexTarget).where(SearchIndexTarget.role == "current").limit(1)
    )
    schema_version = target.schema_version if target else 1
    if criteria.annotation_search and schema_version < 2:
        raise HTTPException(
            409,
            {
                "code": "search_upgrade_required",
                "message": "Rebuild search to schema version 2 to use annotation filters",
            },
        )
    return (target.index_name if target else ALIAS), schema_version


def text_fields(schema_version: int) -> list[str]:
    fields = ["title^3", "descriptions", "body"]
    if schema_version >= 2:
        fields.extend(["entity_text", "keyword_text"])
    return fields


def build_query(criteria: SearchCriteria, schema_version: int) -> dict[str, Any]:
    fields = text_fields(schema_version)
    must: list[dict[str, Any]] = [
        {"multi_match": {"query": term, "fields": fields, "operator": "and"}}
        for term in criteria.parsed.terms
    ]
    must.extend(
        {"multi_match": {"query": phrase, "fields": fields, "type": "phrase"}}
        for phrase in criteria.parsed.phrases
    )
    filters: list[dict[str, Any]] = []
    provenance_must: list[dict[str, Any]] = []
    if criteria.sources:
        provenance_must.append({"terms": {"provenance.source_id": criteria.sources}})
    if criteria.countries:
        provenance_must.append({"terms": {"provenance.source_country": criteria.countries}})
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
        entity_must.append({"terms": {"entities.id": criteria.entity_ids}})
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
    return {"bool": {"must": must or [{"match_all": {}}], "filter": filters}}
