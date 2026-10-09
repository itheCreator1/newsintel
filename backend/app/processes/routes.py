from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursors import cursor_or_400
from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.processes import activity, summary
from app.processes.schemas import ActivityFilter, ActivityPage, ProcessesResponse, ProcessKey

router = APIRouter(tags=["processes"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
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
