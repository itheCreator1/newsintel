import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.feeds.models import Feed
from app.feeds.service import decode_cursor, encode_cursor
from app.nlp.models import Entity, Keyword
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable
from app.search.models import SearchDelivery, SearchIndexTarget
from app.search.query import SearchSyntaxError, parse_query
from app.search.rebuild import ALIAS, rebuild_status
from app.search.schemas import (
    HighlightSegment,
    IndexFailure,
    IndexFailurePage,
    IndexStatus,
    RetryIndexResponse,
    SearchPage,
    SearchResult,
    SearchSource,
    SearchSourcePage,
)

router = APIRouter(tags=["search"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]
Config = Annotated[Settings, Depends(get_settings)]
Sort = Literal["relevance", "newest", "oldest", "most_sources"]


def _sign_cursor(payload: dict[str, Any], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode()


def _read_cursor(value: str, secret: str) -> dict[str, Any]:
    try:
        decoded = base64.urlsafe_b64decode(value.encode())
        raw, signature = decoded[:-32], decoded[-32:]
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        result: dict[str, Any] = json.loads(raw)
        return result
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "Invalid search cursor; restart the search") from exc


def _segments(values: list[str]) -> list[HighlightSegment]:
    text_value = " … ".join(values)[:1200]
    parts = text_value.split("\ue000")
    output: list[HighlightSegment] = []
    marked = False
    for part in parts:
        if "\ue001" in part:
            marked_text, remainder = part.split("\ue001", 1)
            if marked_text:
                output.append(HighlightSegment(text=marked_text, marked=True))
            if remainder:
                output.append(HighlightSegment(text=remainder))
            marked = False
        elif part:
            output.append(HighlightSegment(text=part, marked=marked))
        marked = True
    return output


@router.get("/search/sources", response_model=SearchSourcePage)
async def search_sources(
    db: Db,
    _auth: Auth,
    q: str = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchSourcePage:
    query = select(Feed).order_by(Feed.name, Feed.id)
    if q.strip():
        query = query.where(Feed.name.ilike(f"%{q.strip()}%"))
    if cursor:
        try:
            name, raw_id = base64.urlsafe_b64decode(cursor).decode().split("|", 1)
            item_id = uuid.UUID(raw_id)
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(422, "Invalid source cursor; restart source selection") from exc
        query = query.where(or_(Feed.name > name, and_(Feed.name == name, Feed.id > item_id)))
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(f"{last.name}|{last.id}".encode()).decode()
    return SearchSourcePage(
        items=[
            SearchSource(
                id=feed.id,
                name=feed.name,
                source_country=feed.source_country,
                retired=feed.retired_at is not None,
            )
            for feed in rows[:limit]
        ],
        next_cursor=next_cursor,
    )


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


@router.get("/search", response_model=SearchPage)
async def search_articles(
    db: Db,
    session: Auth,
    settings: Config,
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
    sort: Sort = "relevance",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> SearchPage:
    try:
        parsed = parse_query(q)
    except SearchSyntaxError as exc:
        raise HTTPException(422, str(exc)) from exc
    sources = sorted(
        {
            *(str(value) for value in source_id or []),
            *(await _resolve_sources(db, parsed.source_values)),
        }
    )
    countries = sorted({value.upper() for value in source_country or []} | set(parsed.countries))
    languages = sorted({value.casefold() for value in language or []} | set(parsed.languages))
    entity_ids = sorted(
        {
            *(str(value) for value in entity_id or []),
            *(await _resolve_annotations(db, parsed.entity_values, Entity)),
        }
    )
    keyword_ids = sorted(
        {
            *(str(value) for value in keyword_id or []),
            *(await _resolve_annotations(db, parsed.keyword_values, Keyword)),
        }
    )
    entity_types = sorted({value.upper() for value in entity_type or []})
    story_countries = sorted(
        {value.upper() for value in story_country or []} | set(parsed.story_countries)
    )
    mentioned_countries = sorted(
        {value.upper() for value in mentioned_country or []} | set(parsed.mentioned_countries)
    )
    start, end = after or parsed.after, before or parsed.before
    criteria = {
        "q": q.strip(),
        "sources": sources,
        "countries": countries,
        "after": str(start or ""),
        "before": str(end or ""),
        "content": content_available,
        "processing": sorted(processing_status or []),
        "languages": languages,
        "entities": entity_ids,
        "entity_types": entity_types,
        "keywords": keyword_ids,
        "story_countries": story_countries,
        "mentioned_countries": mentioned_countries,
        "sort": sort,
        "limit": limit,
    }
    criteria_hash = hashlib.sha256(json.dumps(criteria, sort_keys=True).encode()).hexdigest()
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    search_after: list[Any] | None = None
    annotation_search = bool(
        languages
        or entity_ids
        or entity_types
        or keyword_ids
        or story_countries
        or mentioned_countries
        or parsed.entity_values
        or parsed.keyword_values
    )
    try:
        if cursor:
            cursor_data = _read_cursor(cursor, settings.secret_key)
            if (
                cursor_data.get("session") != str(session.id)
                or cursor_data.get("criteria") != criteria_hash
            ):
                raise HTTPException(422, "Search cursor does not match this session or query")
            if datetime.fromisoformat(cursor_data["expires"]) <= datetime.now(UTC):
                raise HTTPException(
                    409, {"code": "restart_search", "message": "Search snapshot expired"}
                )
            pit_id, search_after = cursor_data["pit"], cursor_data["after"]
            schema_version = int(cursor_data.get("schema", 1))
        else:
            current_target = await db.scalar(
                select(SearchIndexTarget).where(SearchIndexTarget.role == "current").limit(1)
            )
            schema_version = current_target.schema_version if current_target else 1
            if annotation_search and schema_version < 2:
                raise HTTPException(
                    409,
                    {
                        "code": "search_upgrade_required",
                        "message": "Rebuild search to schema version 2 to use annotation filters",
                    },
                )
            pit_id = await adapter.open_point_in_time(
                current_target.index_name if current_target else ALIAS
            )
        text_fields = ["title^3", "descriptions", "body"]
        if schema_version >= 2:
            text_fields.extend(["entity_text", "keyword_text"])
        must: list[dict[str, Any]] = []
        for term in parsed.terms:
            must.append(
                {
                    "multi_match": {
                        "query": term,
                        "fields": text_fields,
                        "operator": "and",
                    }
                }
            )
        for phrase in parsed.phrases:
            must.append(
                {
                    "multi_match": {
                        "query": phrase,
                        "fields": text_fields,
                        "type": "phrase",
                    }
                }
            )
        filters: list[dict[str, Any]] = []
        provenance_must: list[dict[str, Any]] = []
        if sources:
            provenance_must.append({"terms": {"provenance.source_id": sources}})
        if countries:
            provenance_must.append({"terms": {"provenance.source_country": countries}})
        if provenance_must:
            filters.append(
                {"nested": {"path": "provenance", "query": {"bool": {"must": provenance_must}}}}
            )
        if start or end:
            bounds: dict[str, str] = {}
            if start:
                bounds["gte"] = datetime.combine(start, time.min, UTC).isoformat()
            if end:
                bounds["lt"] = datetime.combine(end, time.min, UTC).isoformat()
            filters.append({"range": {"effective_date": bounds}})
        if content_available is not None:
            filters.append({"term": {"content_available": content_available}})
        if processing_status:
            filters.append({"terms": {"processing_status": processing_status}})
        if languages:
            filters.append({"terms": {"detected_language": languages}})
        entity_must: list[dict[str, Any]] = []
        if entity_ids or parsed.entity_values:
            entity_must.append({"terms": {"entities.id": entity_ids}})
        if entity_types:
            entity_must.append({"terms": {"entities.type": entity_types}})
        if entity_must:
            filters.append(
                {"nested": {"path": "entities", "query": {"bool": {"must": entity_must}}}}
            )
        if keyword_ids or parsed.keyword_values:
            filters.append({"terms": {"keyword_ids": keyword_ids}})
        if story_countries:
            filters.append({"terms": {"primary_story_country": story_countries}})
        if mentioned_countries:
            filters.append({"terms": {"mentioned_countries": mentioned_countries}})
        sort_clause: list[Any] = {
            "relevance": [{"_score": "desc"}, {"effective_date": "desc"}],
            "newest": [{"effective_date": "desc"}],
            "oldest": [{"effective_date": "asc"}],
            "most_sources": [{"distinct_source_count": "desc"}, {"effective_date": "desc"}],
        }[sort]
        sort_clause.append({"article_id": "asc"})
        body: dict[str, Any] = {
            "size": limit,
            "pit": {"id": pit_id, "keep_alive": "5m"},
            "query": {"bool": {"must": must or [{"match_all": {}}], "filter": filters}},
            "sort": sort_clause,
            "_source": [
                "article_id",
                "title",
                "effective_date",
                "distinct_source_count",
                "provenance",
            ],
            "highlight": {
                "fields": {
                    "title": {},
                    "descriptions": {},
                    "body": {},
                    **({"entity_text": {}, "keyword_text": {}} if schema_version >= 2 else {}),
                },
                "pre_tags": ["\ue000"],
                "post_tags": ["\ue001"],
                "fragment_size": 220,
                "number_of_fragments": 2,
            },
        }
        if search_after:
            body["search_after"] = search_after
        response = await adapter.search(body)
    except ElasticsearchUnavailable as exc:
        message = str(exc)
        if cursor and ("404" in message or "point in time" in message.lower()):
            raise HTTPException(
                409, {"code": "restart_search", "message": "Search snapshot expired"}
            ) from exc
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
    hits = response.get("hits", {}).get("hits", [])
    items: list[SearchResult] = []
    for hit in hits:
        source = hit["_source"]
        highlight_values = [
            value for values in hit.get("highlight", {}).values() for value in values
        ]
        segments = _segments(highlight_values)
        items.append(
            SearchResult(
                article_id=source["article_id"],
                title=source["title"],
                effective_date=source["effective_date"],
                distinct_source_count=source["distinct_source_count"],
                sources=sorted({item["source_name"] for item in source["provenance"]}),
                summary="".join(segment.text for segment in segments)[:500] or None,
                highlights=segments,
            )
        )
    next_cursor = None
    if len(hits) == limit:
        payload = {
            "pit": response.get("pit_id", pit_id),
            "after": hits[-1]["sort"],
            "session": str(session.id),
            "criteria": criteria_hash,
            "expires": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "schema": schema_version,
        }
        next_cursor = _sign_cursor(payload, settings.secret_key)
    return SearchPage(items=items, next_cursor=next_cursor)


@router.get("/search/indexing/status", response_model=IndexStatus)
async def indexing_status(db: Db, _auth: Auth) -> IndexStatus:
    rows = (
        await db.execute(
            select(SearchDelivery.status, func.count()).group_by(SearchDelivery.status)
        )
    ).all()
    counts = {name: count for name, count in rows}
    rebuilds = await rebuild_status()
    return IndexStatus(
        **{name: counts.get(name, 0) for name in ("queued", "running", "retrying", "failed")},
        active_rebuild=next((row for row in rebuilds if row["status"] != "completed"), None),
    )


@router.get("/search/indexing/failures", response_model=IndexFailurePage)
async def indexing_failures(
    db: Db, _auth: Auth, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> IndexFailurePage:
    query = (
        select(SearchDelivery, SearchIndexTarget)
        .join(SearchIndexTarget)
        .where(SearchDelivery.status == "failed")
        .order_by(SearchDelivery.updated_at.desc(), SearchDelivery.id.desc())
    )
    if cursor:
        updated, item_id = decode_cursor(cursor)
        query = query.where(
            or_(
                SearchDelivery.updated_at < updated,
                and_(SearchDelivery.updated_at == updated, SearchDelivery.id < item_id),
            )
        )
    rows = (await db.execute(query.limit(limit + 1))).all()
    next_cursor = (
        encode_cursor(rows[limit - 1][0].updated_at, rows[limit - 1][0].id)
        if len(rows) > limit
        else None
    )
    return IndexFailurePage(
        items=[
            IndexFailure(
                id=item.id,
                article_id=item.article_id,
                index_name=target.index_name,
                error_category=item.error_category,
                error_message=item.error_message,
                attempt_count=item.attempt_count,
                updated_at=item.updated_at,
            )
            for item, target in rows[:limit]
        ],
        next_cursor=next_cursor,
    )


@router.post("/search/indexing/articles/{article_id}/retry", response_model=RetryIndexResponse)
async def retry_indexing(article_id: uuid.UUID, db: Db, _mutation: Mutation) -> RetryIndexResponse:
    rows = list(
        (
            await db.scalars(
                select(SearchDelivery)
                .where(SearchDelivery.article_id == article_id, SearchDelivery.status == "failed")
                .with_for_update()
            )
        ).all()
    )
    if not rows:
        raise HTTPException(404, "No failed indexing delivery for this article")
    for delivery in rows:
        delivery.status = "queued"
        delivery.next_attempt_at = datetime.now(UTC)
        delivery.error_category = None
        delivery.error_message = None
    await db.commit()
    return RetryIndexResponse(status="queued")
