import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import issue_csrf_token, verify_csrf_token
from app.auth.models import Session, User
from app.auth.schemas import CsrfResponse, LoginRequest, UserResponse
from app.auth.service import authenticate, create_session, get_session, revoke_session
from app.core.config import Settings, get_settings
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


async def current_session(
    request: Request,
    db: Db,
    settings: Config,
) -> Session:
    token = request.cookies.get(settings.session_cookie_name)
    record = await get_session(db, token or "")
    if not record:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return record


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(
    request: Request,
    db: Db,
    settings: Config,
) -> CsrfResponse:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        record = await get_session(db, token)
        if record:
            return CsrfResponse(csrf_token=record.csrf_token)
    return CsrfResponse(csrf_token=issue_csrf_token(settings.secret_key))


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Db,
    settings: Config,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> UserResponse:
    if not csrf_token or not verify_csrf_token(csrf_token, settings.secret_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    user = await authenticate(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token, _ = await create_session(db, user, settings)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_lifetime_hours * 3600,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return UserResponse(id=user.id, username=user.username)


@router.get("/me", response_model=UserResponse)
async def me(record: Annotated[Session, Depends(current_session)], db: Db) -> UserResponse:
    user = await db.get(User, record.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return UserResponse(id=user.id, username=user.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Db,
    settings: Config,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    record = await get_session(db, token or "")
    if not record or not csrf_token or not secrets.compare_digest(record.csrf_token, csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    await revoke_session(db, token or "")
    response.delete_cookie(settings.session_cookie_name, path="/")
