import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.service import get_session
from app.core.config import Settings, get_settings
from app.db.session import get_db


async def current_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Session:
    token = request.cookies.get(settings.session_cookie_name)
    record = await get_session(db, token or "")
    if not record:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return record


async def require_csrf(
    session: Annotated[Session, Depends(current_session)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Session:
    if not csrf_token or not secrets.compare_digest(session.csrf_token, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    return session
