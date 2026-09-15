import argparse
import asyncio
import getpass
import uuid
from datetime import timedelta

from sqlalchemy import delete, select

from app.articles.maintenance import cleanup_article_storage
from app.articles.storage import LocalObjectStorage
from app.auth.models import Session, User
from app.auth.security import hash_password
from app.auth.service import normalize_username
from app.clustering.service import count_recluster_selection, run_recluster
from app.core.config import get_settings
from app.db.session import session_factory
from app.nlp.reprocessing import (
    count_selection,
    create_reprocessing_run,
    reprocessing_status,
    scan_reprocessing,
)
from app.nlp.service import PROCESSORS
from app.search.rebuild import create_rebuild, rebuild_status, scan_rebuild, try_cutover


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


async def cleanup_storage(*, apply: bool, batch_size: int, complete_sweep: bool) -> None:
    settings = get_settings()
    async with session_factory() as db:
        report = await cleanup_article_storage(
            db,
            LocalObjectStorage(settings.article_storage_path),
            minimum_age=timedelta(hours=settings.article_temporary_html_hours),
            apply=apply,
            batch_size=batch_size,
            complete_sweep=complete_sweep,
        )
    mode = "apply" if apply else "dry-run"
    print(
        f"{mode}: scanned={report.scanned} eligible={report.eligible} "
        f"deleted={report.deleted} failed={report.failed}"
    )
    for failure in report.failures:
        print(f"failed object {failure.key}: {failure.error}")


async def run_search_rebuild(rebuild_id: uuid.UUID | None = None) -> None:
    active_id = rebuild_id or await create_rebuild()
    while await scan_rebuild(active_id):
        pass
    cutover = await try_cutover(active_id)
    print(f"rebuild_id={active_id} status={'completed' if cutover else 'catching_up'}")


async def print_search_status() -> None:
    rows = await rebuild_status()
    if not rows:
        print("No search rebuilds have been started")
    for row in rows:
        print(f"{row['id']} status={row['status']} index={row['index']} scanned={row['scanned']}")


async def run_nlp_reprocessing(
    *,
    run_id: uuid.UUID | None,
    processors: tuple[str, ...],
    selection: dict[str, object],
    apply: bool,
) -> None:
    if run_id is None and not apply:
        count = await count_selection(selection)
        print(f"dry-run: articles={count} processors={','.join(processors)}")
        return
    active_id = run_id or await create_reprocessing_run(
        processor_names=processors, selection=selection
    )
    while await scan_reprocessing(active_id):
        pass
    print(f"run_id={active_id} status=succeeded")


async def print_nlp_status() -> None:
    rows = await reprocessing_status()
    if not rows:
        print("No NLP reprocessing runs have been started")
        return
    for row in rows:
        print(
            f"{row['id']} status={row['status']} scanned={row['scanned']} "
            f"enqueued={row['enqueued']} jobs={row['jobs']}"
        )


async def run_clustering_selection(*, selection: dict[str, object], apply: bool) -> None:
    if not apply:
        count = await count_recluster_selection(selection)
        print(f"dry-run: articles={count}")
        return
    queued = await run_recluster(selection)
    print(f"recluster: jobs_queued={queued}")


def _selection_from(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, object]:
    selection: dict[str, object] = {}
    if args.article_id:
        try:
            selection["article_ids"] = [str(uuid.UUID(value)) for value in args.article_id]
        except ValueError:
            parser.error("--article-id values must be UUIDs")
    elif args.all:
        selection["all"] = True
    else:
        if args.from_date:
            selection["from_date"] = args.from_date
        if args.to_date:
            selection["to_date"] = args.to_date
    return selection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "create-user",
            "reset-password",
            "cleanup-article-storage",
            "rebuild-search",
            "search-index-status",
            "resume-search-rebuild",
            "reprocess-nlp",
            "nlp-status",
            "resume-nlp-reprocessing",
            "recluster",
        ],
    )
    parser.add_argument("username", nargs="?")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--complete-sweep", action="store_true")
    parser.add_argument("--processors", nargs="+", choices=PROCESSORS, default=list(PROCESSORS))
    parser.add_argument("--article-id", action="append", default=[])
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.command == "nlp-status":
        if args.username:
            parser.error("nlp-status does not accept an argument")
        asyncio.run(print_nlp_status())
        return
    if args.command == "resume-nlp-reprocessing":
        if not args.username:
            parser.error("resume-nlp-reprocessing requires RUN_ID")
        try:
            run_id = uuid.UUID(args.username)
        except ValueError:
            parser.error("RUN_ID must be a UUID")
        asyncio.run(
            run_nlp_reprocessing(
                run_id=run_id,
                processors=tuple(args.processors),
                selection={},
                apply=True,
            )
        )
        return
    if args.command == "reprocess-nlp":
        if args.username:
            parser.error("reprocess-nlp does not accept an argument")
        selection_modes = (
            int(bool(args.article_id)) + int(bool(args.from_date or args.to_date)) + int(args.all)
        )
        if selection_modes != 1:
            parser.error(
                "reprocess-nlp requires exactly one of --article-id, a date range, or --all"
            )
        asyncio.run(
            run_nlp_reprocessing(
                run_id=None,
                processors=tuple(args.processors),
                selection=_selection_from(args, parser),
                apply=args.apply,
            )
        )
        return
    if args.command == "recluster":
        if args.username:
            parser.error("recluster does not accept an argument")
        selection_modes = (
            int(bool(args.article_id)) + int(bool(args.from_date or args.to_date)) + int(args.all)
        )
        if selection_modes != 1:
            parser.error("recluster requires exactly one of --article-id, a date range, or --all")
        asyncio.run(
            run_clustering_selection(
                selection=_selection_from(args, parser), apply=args.apply
            )
        )
        return
    if args.command == "rebuild-search":
        if args.username:
            parser.error("rebuild-search does not accept an argument")
        asyncio.run(run_search_rebuild())
        return
    if args.command == "search-index-status":
        if args.username:
            parser.error("search-index-status does not accept an argument")
        asyncio.run(print_search_status())
        return
    if args.command == "resume-search-rebuild":
        if not args.username:
            parser.error("resume-search-rebuild requires REBUILD_ID")
        try:
            rebuild_id = uuid.UUID(args.username)
        except ValueError:
            parser.error("REBUILD_ID must be a UUID")
        asyncio.run(run_search_rebuild(rebuild_id))
        return
    if args.command == "cleanup-article-storage":
        if args.username or args.batch_size < 1 or args.batch_size > 5_000:
            parser.error("cleanup batch size must be between 1 and 5000")
        asyncio.run(
            cleanup_storage(
                apply=args.apply,
                batch_size=args.batch_size,
                complete_sweep=args.complete_sweep,
            )
        )
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
