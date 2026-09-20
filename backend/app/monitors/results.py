import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.monitors.evaluation import criteria_params, window_filter
from app.monitors.models import Monitor
from app.monitors.schemas import MonitorResultPage
from app.monitors.service import parse_state
from app.search.criteria import build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable
from app.search.routes import RESULT_FIELDS, read_cursor, search_result, sign_cursor

Scope = Literal["unseen", "recent"]


def results_body(
    query: dict[str, Any],
    after: datetime | None,
    upto: datetime,
    limit: int,
    search_after: list[Any] | None,
) -> dict[str, Any]:
    """The articles first discovered in `(after, upto]`, newest first, one page."""
    body: dict[str, Any] = {
        "size": limit,
        "query": window_filter(query, after, upto),
        "sort": [{"first_discovered_at": "desc"}, {"article_id": "desc"}],
        "_source": RESULT_FIELDS,
    }
    if search_after:
        body["search_after"] = search_after
    return body


def encode_results_cursor(
    secret: str,
    session_id: uuid.UUID,
    monitor_id: uuid.UUID,
    scope: Scope,
    after: datetime | None,
    upto: datetime,
    sort: list[Any],
) -> str:
    return sign_cursor(
        {
            "session": str(session_id),
            "monitor": str(monitor_id),
            "scope": scope,
            "after": after.isoformat() if after else None,
            "upto": upto.isoformat(),
            "sort": sort,
        },
        secret,
    )


def decode_results_cursor(
    secret: str, value: str, session_id: uuid.UUID, monitor_id: uuid.UUID, scope: Scope
) -> tuple[datetime | None, datetime, list[Any]]:
    data = read_cursor(value, secret)
    try:
        if (data["session"], data["monitor"], data["scope"]) != (
            str(session_id),
            str(monitor_id),
            scope,
        ):
            raise ValueError
        after = datetime.fromisoformat(data["after"]) if data["after"] else None
        return after, datetime.fromisoformat(data["upto"]), list(data["sort"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Cursor does not match this monitor or view") from exc


async def monitor_results(
    db: AsyncSession,
    item: Monitor,
    session_id: uuid.UUID,
    scope: Scope,
    limit: int,
    cursor: str | None,
) -> MonitorResultPage:
    secret = get_settings().secret_key
    if cursor:
        after, upto, search_after = decode_results_cursor(
            secret, cursor, session_id, item.id, scope
        )
    elif item.eval_cursor_at is None:
        return MonitorResultPage(items=[], next_cursor=None, window_start=None, window_end=None)
    else:
        after = item.viewed_cursor_at if scope == "unseen" else None
        upto, search_after = item.eval_cursor_at, []
    state, problem = parse_state(item)
    if state is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "invalid_monitor_state", "message": problem}
        )
    criteria = await search_criteria(db, **criteria_params(state))
    index_name, schema_version = await current_search_target(db, criteria)
    body = results_body(build_query(criteria, schema_version), after, upto, limit, search_after)
    try:
        response = await ElasticsearchAdapter(get_settings().elasticsearch_url).search_index(
            index_name, body
        )
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
    hits = response["hits"]["hits"]
    next_cursor = (
        encode_results_cursor(secret, session_id, item.id, scope, after, upto, hits[-1]["sort"])
        if len(hits) == limit
        else None
    )
    return MonitorResultPage(
        items=[search_result(hit) for hit in hits],
        next_cursor=next_cursor,
        window_start=after,
        window_end=upto,
    )
