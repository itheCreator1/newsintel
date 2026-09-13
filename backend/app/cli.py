import argparse
import asyncio
import getpass

from sqlalchemy import delete, select

from app.auth.models import Session, User
from app.auth.security import hash_password
from app.auth.service import normalize_username
from app.db.session import session_factory


async def create_user(username: str) -> None:
    normalized = normalize_username(username)
    password = getpass.getpass("Password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12 or password != confirmation:
        raise SystemExit("Passwords must match and contain at least 12 characters")
    async with session_factory() as db:
        if await db.scalar(select(User).where(User.username == normalized)):
            raise SystemExit("User already exists")
        db.add(User(username=normalized, password_hash=hash_password(password)))
        await db.commit()
    print(f"Created user {normalized}")


async def reset_password(username: str) -> None:
    normalized = normalize_username(username)
    password = getpass.getpass("New password (minimum 12 characters): ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.username == normalized))
        if not user:
            raise SystemExit("User not found")
        user.password_hash = hash_password(password)
        await db.execute(delete(Session).where(Session.user_id == user.id))
        await db.commit()
    print(f"Reset password for {normalized} and revoked sessions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-user", "reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    asyncio.run(
        create_user(args.username)
        if args.command == "create-user"
        else reset_password(args.username)
    )


if __name__ == "__main__":
    main()
