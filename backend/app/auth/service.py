import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session, User
from app.auth.security import hash_password, verify_password
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


def _others(record: Session, now: datetime) -> list[ColumnElement[bool]]:
    return [
        Session.user_id == record.user_id,
        Session.id != record.id,
        Session.revoked_at.is_(None),
        Session.expires_at > now,
    ]


async def count_other_sessions(db: AsyncSession, record: Session) -> int:
    now = datetime.now(UTC)
    query = select(func.count()).select_from(Session).where(*_others(record, now))
    return await db.scalar(query) or 0


async def revoke_other_sessions(db: AsyncSession, record: Session) -> int:
    """Sign out every other browser of this account; this one stays signed in."""
    now = datetime.now(UTC)
    revoked = await db.scalars(
        update(Session).where(*_others(record, now)).values(revoked_at=now).returning(Session.id)
    )
    count = len(revoked.all())
    await db.commit()
    return count


async def change_password(db: AsyncSession, user: User, record: Session, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    await revoke_other_sessions(db, record)
