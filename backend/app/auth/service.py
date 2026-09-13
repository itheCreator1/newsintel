import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session, User
from app.auth.security import verify_password
from app.auth.sessions import generate_session_token, hash_session_token
from app.core.config import Settings


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def session_is_active(expires_at: datetime, revoked_at: datetime | None, now: datetime) -> bool:
    return revoked_at is None and expires_at > now


async def authenticate(db: AsyncSession, username: str, password: str) -> User | None:
    user = await db.scalar(select(User).where(User.username == normalize_username(username)))
    return user if user and verify_password(password, user.password_hash) else None


async def create_session(db: AsyncSession, user: User, settings: Settings) -> tuple[str, Session]:
    token = generate_session_token()
    record = Session(
        token_hash=hash_session_token(token),
        csrf_token=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_lifetime_hours),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return token, record


async def get_session(db: AsyncSession, token: str) -> Session | None:
    record = await db.scalar(select(Session).where(Session.token_hash == hash_session_token(token)))
    if record and session_is_active(record.expires_at, record.revoked_at, datetime.now(UTC)):
        return record
    return None


async def revoke_session(db: AsyncSession, token: str) -> None:
    await db.execute(
        update(Session)
        .where(Session.token_hash == hash_session_token(token), Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
