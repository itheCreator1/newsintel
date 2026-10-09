import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursors import cursor_or_400
from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.feeds.service import QueueUnavailable
from app.processes import actions, activity, summary
from app.processes.schemas import (
    ActivityFilter,
    ActivityPage,
    ProcessesResponse,
    ProcessKey,
    RetryFailedResponse,
    RetryItemResponse,
    RunNowResponse,
    StopResponse,
)

router = APIRouter(tags=["processes"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Mutation = Annotated[Session, Depends(require_csrf)]
Senders = Annotated[actions.Senders, Depends(actions.default_senders)]
Hours = Annotated[
    int, Query(ge=1, le=168, description="Window length in hours, ending now; it bounds what "
               "finished, while what is still queued or running always counts.")
]  # fmt: skip


@router.get("/processes", response_model=ProcessesResponse)
async def processes(db: Db, _auth: Auth, settings: Config, hours: Hours = 24) -> ProcessesResponse:
    """One card per background process: its counts, its newest run and its state."""
    return await summary.summary(db, datetime.now(UTC), hours, settings)


@router.get("/processes/activity", response_model=ActivityPage)
async def process_activity(
    db: Db,
    auth: Auth,
    hours: Hours = 24,
    process: ProcessKey | None = None,
    status: ActivityFilter = "attention",
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ActivityPage:
    """What every process did or is doing, newest first. `attention` is what is running or
    retrying now and what failed in the window."""
    return await activity.activity(
        db, datetime.now(UTC), hours, process=process, status=status,
        q=q.strip() if q and q.strip() else None, after=cursor_or_400(cursor), limit=limit,
        user_id=auth.user_id,
    )  # fmt: skip


def _refused(refused: actions.Refused) -> HTTPException:
    return HTTPException(refused.status_code, refused.detail)


QUEUE_DOWN = "The job queue is unavailable"


@router.post(
    "/processes/activity/{key}/{item_id}/retry", response_model=RetryItemResponse,
    status_code=status.HTTP_202_ACCEPTED,
)  # fmt: skip
async def retry_item(
    key: ProcessKey, item_id: uuid.UUID, db: Db, _mutation: Mutation, senders: Senders
) -> RetryItemResponse:
    """Try one failed row of the activity list again."""
    try:
        retried = await actions.retry_item(db, key, item_id, senders)
    except actions.Refused as refused:
        raise _refused(refused) from None
    except QueueUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, QUEUE_DOWN) from exc
    if not retried:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No failed item to retry")
    await db.commit()
    return RetryItemResponse(status="queued")


@router.post(
    "/processes/{key}/retry-failed", response_model=RetryFailedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)  # fmt: skip
async def retry_failed(
    key: ProcessKey, db: Db, _mutation: Mutation, senders: Senders
) -> RetryFailedResponse:
    """Try what failed again, a bounded batch at a time; `remaining` says how many still wait."""
    try:
        retried, remaining = await actions.retry_failed(db, key, senders)
    except actions.Refused as refused:
        raise _refused(refused) from None
    await db.commit()
    return RetryFailedResponse(retried=retried, remaining=remaining)


@router.post(
    "/processes/{key}/run", response_model=RunNowResponse, status_code=status.HTTP_202_ACCEPTED
)
async def run_now(
    key: ProcessKey, db: Db, _mutation: Mutation, settings: Config, senders: Senders
) -> RunNowResponse:
    """Run a scheduled process now instead of at its next turn."""
    try:
        done = await actions.run_now(db, key, settings, senders)
    except actions.Refused as refused:
        raise _refused(refused) from None
    await db.commit()
    return RunNowResponse(status=done.status, run_id=done.run_id)


@router.post("/processes/{key}/runs/{run_id}/stop", response_model=StopResponse)
async def stop_run(key: ProcessKey, run_id: uuid.UUID, db: Db, _mutation: Mutation) -> StopResponse:
    """Stop a run part way; what it already did stays done."""
    try:
        stopped = await actions.stop(db, key, run_id)
    except actions.Refused as refused:
        raise _refused(refused) from None
    if not stopped:
        raise HTTPException(status.HTTP_409_CONFLICT, "This run is not running")
    await db.commit()
    return StopResponse(status="stopped")
