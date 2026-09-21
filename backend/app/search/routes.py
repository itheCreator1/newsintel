import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
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
from app.search.criteria import SearchCriteria, build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable
from app.search.models import SearchDelivery, SearchIndexTarget
from app.search.rebuild import rebuild_status
from app.search.schemas import (
    HighlightSegment,
    IndexFailure,
    IndexFailurePage,
    IndexStatus,
    RetryIndexResponse,
    SearchPage,
    SearchResult,
    SearchResultSource,
    SearchSource,
    SearchSourcePage,
    SearchTimeline,
    StoryClusterRef,
    TimelineBucket,
)
from app.search.timeline import (
    RequestedInterval,
    TimelineTooFine,
    histogram_bounds,
    select_interval,
)

router = APIRouter(tags=["search"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]
Config = Annotated[Settings, Depends(get_settings)]
Criteria = Annotated[SearchCriteria, Depends(search_criteria)]
Sort = Literal["relevance", "newest", "oldest", "most_sources"]


RESULT_FIELDS = [
    "article_id",
    "title",
    "effective_date",
    "distinct_source_count",
    "provenance",
    "primary_story_country",
    "story_cluster_id",
    "cluster_source_count",
]


def sign_cursor(payload: dict[str, Any], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode()


def read_cursor(value: str, secret: str) -> dict[str, Any]:
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


def search_result(hit: dict[str, Any]) -> SearchResult:
    """One Elasticsearch article hit as a result; highlights appear only if the hit has them."""
    source = hit["_source"]
    highlight_values = [value for values in hit.get("highlight", {}).values() for value in values]
    segments = _segments(highlight_values)
    refs = {
        item["source_id"]: SearchResultSource(
            id=item["source_id"], name=item["source_name"], country=item.get("source_country")
        )
        for item in source["provenance"]
    }
    return SearchResult(
        article_id=source["article_id"],
        title=source["title"],
        effective_date=source["effective_date"],
        distinct_source_count=source["distinct_source_count"],
        sources=sorted({item["source_name"] for item in source["provenance"]}),
        source_refs=sorted(refs.values(), key=lambda ref: (ref.name.lower(), str(ref.id))),
        story_country=source.get("primary_story_country"),
        story_cluster=(
            StoryClusterRef(
                id=source["story_cluster_id"],
                source_count=source["cluster_source_count"],
            )
            if source.get("story_cluster_id")
            else None
        ),
        summary="".join(segment.text for segment in segments)[:500] or None,
        highlights=segments,
    )


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


@router.get("/search", response_model=SearchPage)
async def search_articles(
    db: Db,
    session: Auth,
    settings: Config,
    criteria: Criteria,
    sort: Sort = "relevance",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> SearchPage:
    fingerprint = {**criteria.fingerprint(), "sort": sort, "limit": limit}
    criteria_hash = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    search_after: list[Any] | None = None
    try:
        if cursor:
            cursor_data = read_cursor(cursor, settings.secret_key)
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
            index_name, schema_version = await current_search_target(db, criteria)
            pit_id = await adapter.open_point_in_time(index_name)
        sort_clause: list[Any] = {
            "relevance": [{"_score": "desc"}, {"effective_date": "desc"}],
            "newest": [{"effective_date": "desc"}],
            "oldest": [{"effective_date": "asc"}],
            "most_sources": [{"distinct_source_count": "desc"}, {"effective_date": "desc"}],
        }[sort]
        sort_clause.append({"article_id": "asc"})
        body: dict[str, Any] = {
            "size": limit + 1,  # the extra hit reveals whether a page follows
            "pit": {"id": pit_id, "keep_alive": "5m"},
            "query": build_query(criteria, schema_version),
            "sort": sort_clause,
            "_source": RESULT_FIELDS,
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
    found = response.get("hits", {}).get("hits", [])
    hits = found[:limit]
    items = [search_result(hit) for hit in hits]
    pit_id = response.get("pit_id", pit_id)
    next_cursor = None
    if len(found) <= limit:
        # The last page: release the snapshot now; its keep_alive only covers abandoned searches.
        # A replay of this page's cursor then gets the existing restart_search response.
        try:
            await adapter.close_point_in_time(pit_id)
        except ElasticsearchUnavailable:
            pass
    else:
        payload = {
            "pit": pit_id,
            "after": hits[-1]["sort"],
            "session": str(session.id),
            "criteria": criteria_hash,
            "expires": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "schema": schema_version,
        }
        next_cursor = sign_cursor(payload, settings.secret_key)
    return SearchPage(items=items, next_cursor=next_cursor)


@router.get("/search/timeline", response_model=SearchTimeline)
async def search_timeline(
    db: Db,
    _auth: Auth,
    settings: Config,
    criteria: Criteria,
    interval: RequestedInterval = "auto",
) -> SearchTimeline:
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    try:
        index_name, schema_version = await current_search_target(db, criteria)
        query = build_query(criteria, schema_version)
        extent = await adapter.search_index(
            index_name,
            {
                "size": 0,
                "track_total_hits": False,
                "query": query,
                "aggs": {
                    "first": {"min": {"field": "effective_date"}},
                    "last": {"max": {"field": "effective_date"}},
                },
            },
        )
        data_min = extent.get("aggregations", {}).get("first", {}).get("value")
        data_max = extent.get("aggregations", {}).get("last", {}).get("value")
        if data_min is None or data_max is None:
            return SearchTimeline(
                interval="day" if interval == "auto" else interval, total=0, buckets=[]
            )
        first, last = histogram_bounds(
            criteria,
            datetime.fromtimestamp(data_min / 1000, UTC),
            datetime.fromtimestamp(data_max / 1000, UTC),
        )
        try:
            chosen = select_interval(interval, first, last)
        except TimelineTooFine as exc:
            raise HTTPException(422, {"code": "timeline_too_fine", "message": str(exc)}) from exc
        response = await adapter.search_index(
            index_name,
            {
                "size": 0,
                "track_total_hits": False,
                "query": query,
                "aggs": {
                    "timeline": {
                        "date_histogram": {
                            "field": "effective_date",
                            "calendar_interval": chosen,
                            "time_zone": "UTC",
                            "min_doc_count": 0,
                            "extended_bounds": {
                                "min": int(first.timestamp() * 1000),
                                "max": int(last.timestamp() * 1000),
                            },
                        }
                    }
                },
            },
        )
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
    buckets = [
        TimelineBucket(
            start=datetime.fromtimestamp(item["key"] / 1000, UTC), count=item["doc_count"]
        )
        for item in response.get("aggregations", {}).get("timeline", {}).get("buckets", [])
    ]
    return SearchTimeline(
        interval=chosen, total=sum(bucket.count for bucket in buckets), buckets=buckets
    )


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
