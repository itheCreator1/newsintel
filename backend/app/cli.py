import argparse
import asyncio
import getpass
from datetime import timedelta

from sqlalchemy import delete, select

from app.articles.maintenance import cleanup_article_storage
from app.articles.storage import LocalObjectStorage
from app.auth.models import Session, User
from app.auth.security import hash_password
from app.auth.service import normalize_username
from app.core.config import get_settings
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


async def cleanup_storage(*, apply: bool, batch_size: int) -> None:
    settings = get_settings()
    async with session_factory() as db:
        report = await cleanup_article_storage(
            db,
            LocalObjectStorage(settings.article_storage_path),
            minimum_age=timedelta(hours=settings.article_temporary_html_hours),
            apply=apply,
            batch_size=batch_size,
        )
    mode = "apply" if apply else "dry-run"
    print(
        f"{mode}: scanned={report.scanned} eligible={report.eligible} "
        f"deleted={report.deleted} failed={report.failed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["create-user", "reset-password", "cleanup-article-storage"]
    )
    parser.add_argument("username", nargs="?")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.command == "cleanup-article-storage":
        if args.username or args.batch_size < 1 or args.batch_size > 5_000:
            parser.error("cleanup batch size must be between 1 and 5000")
        asyncio.run(cleanup_storage(apply=args.apply, batch_size=args.batch_size))
        return
    if not args.username:
        parser.error("username is required")
    action = (
        create_user(args.username)
        if args.command == "create-user"
        else reset_password(args.username)
    )
    asyncio.run(action)


if __name__ == "__main__":
    main()
