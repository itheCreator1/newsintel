import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.auth.models import Session
from app.auth.routes import current_session


async def require_csrf(
    session: Annotated[Session, Depends(current_session)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Session:
    if not csrf_token or not secrets.compare_digest(session.csrf_token, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    return session
