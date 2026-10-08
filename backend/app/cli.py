import argparse
import asyncio
import getpass
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.articles.maintenance import cleanup_article_storage
from app.articles.storage import LocalObjectStorage
from app.auth.models import Session, User
from app.auth.security import hash_password
from app.auth.service import normalize_username
from app.clustering.service import (
    MAX_RECLUSTER_BATCH,
    count_recluster_selection,
    run_recluster,
    validate_selection,
)
from app.core.config import Settings, get_settings
from app.db.session import session_factory
from app.entities.transfer import export_authorities, import_authorities, parse_file
from app.nlp.authority import seed_countries
from app.nlp.reprocessing import (
    count_selection,
    create_reprocessing_run,
    reprocessing_status,
    scan_reprocessing,
)
from app.nlp.service import PROCESSORS
from app.search.rebuild import create_rebuild, rebuild_status, scan_rebuild, try_cutover
from app.wikidata import runs as wikidata_runs
from app.wikidata.client import WikidataClient
from app.wikidata.links import queue_fetch
from app.wikidata.models import EntityExternalId, WikidataRun
from app.wikidata.status import disabled_reason, wikidata_status
from app.wikidata.throttle import PostgresThrottle


async def create_user(username: str, password: str | None = None) -> None:
    normalized = normalize_username(username)
    if password is None:
        password = getpass.getpass("Password (minimum 12 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords must match and contain at least 12 characters")
    if len(password) < 12:
        raise SystemExit("Passwords must match and contain at least 12 characters")
    async with session_factory() as db:
        if await db.scalar(select(User).where(User.username == normalized)):
            raise SystemExit("User already exists")
        db.add(User(username=normalized, password_hash=hash_password(password)))
        await db.commit()
    print(f"Created user {normalized}")


async def bootstrap_admin() -> None:
    """First-run account from the environment; never touches an install that already has users."""
    username = os.environ.get("NEWSINTEL_ADMIN_USERNAME", "").strip()
    password = os.environ.get("NEWSINTEL_ADMIN_PASSWORD") or ""
    if not username or not password:
        print("No first-run account configured (NEWSINTEL_ADMIN_USERNAME/NEWSINTEL_ADMIN_PASSWORD)")
        return
    async with session_factory() as db:
        if await db.scalar(select(User.id).limit(1)):
            print("Users already exist; first-run account skipped")
            return
    await create_user(username, password)


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


AUTHORITY_ACTIONS = ("seed-countries", "export", "import")


async def run_authority_export(path: Path | None, *, language: str | None) -> None:
    """Write the authority file as JSON to `path`, or to stdout without one."""
    async with session_factory() as db:
        data = await export_authorities(db, language=language)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    await asyncio.to_thread(path.write_text, text, encoding="utf-8")
    print(
        f"authority export: {len(data['entities'])} roots, {len(data['distinct'])} distinct pairs"
    )


async def run_authority_import(path: Path, *, apply: bool) -> None:
    """Apply an authority file; without --apply the changes are rolled back and only reported."""
    try:
        data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        parse_file(data)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot read the authority file {path}: {error}") from None
    async with session_factory() as db:
        report = await import_authorities(db, data)
        if apply:
            await db.commit()
        else:
            await db.rollback()
    mode = "applied" if apply else "dry-run (use --apply to write)"
    print(f"authority import {mode}: {report.summary()}")
    for item in report.conflicts:
        print(f"  conflict {item}")


async def run_seed_countries(*, apply: bool) -> None:
    async with session_factory() as db:
        report = await seed_countries(db)
        if apply:
            await db.commit()
        else:
            await db.rollback()
    mode = "applied" if apply else "dry run (use --apply to write)"
    print(
        f"seed-countries {mode}: {report.created} created, {report.linked} linked, "
        f"{report.already_linked} already linked, {len(report.skipped)} skipped"
    )
    for item in report.skipped:
        print(f"  skipped {item}")


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


async def run_clustering_selection(
    *, selection: dict[str, object], apply: bool, batch_size: int
) -> None:
    if not apply:
        count = await count_recluster_selection(selection)
        print(f"dry-run: articles={count}")
        return
    queued = await run_recluster(selection, batch_size=batch_size)
    print(f"recluster: jobs_queued={queued}")


WIKIDATA_ACTIONS = ("refresh", "status")


def _run_line(run: WikidataRun) -> str:
    line = (
        f"wikidata {run.kind} {run.id}: {run.status} checked={run.checked} changed={run.changed}"
        f" redirected={run.redirected} missing={run.missing} requests={run.requests}"
    )
    return f"{line} error={run.error}" if run.error else line


async def _work_wikidata_run(run_id: uuid.UUID, client: WikidataClient, settings: Settings) -> None:
    """Do the run here, batch after batch, until it ends or Wikidata asks us to wait."""
    while True:
        async with session_factory() as db, db.begin():
            claimed = await wikidata_runs.claim_run(db, settings, run_id=run_id)
            waiting = await wikidata_runs.blocked(db, settings, datetime.now(UTC))
        if claimed is None:
            print(f"wikidata run {run_id} waits in the queue: {waiting or 'another worker has it'}")
            return
        await wikidata_runs.process_run(claimed[0], claimed[1], client, settings)
        async with session_factory() as db:
            run = await db.get(WikidataRun, run_id)
        assert run is not None
        if run.status != "queued" or run.error:
            print(_run_line(run))
            if run.status == "queued":
                print("  the run waits in the queue; the scheduler goes on with it")
            return


async def run_wikidata_refresh(
    *,
    qids: list[str],
    everything: bool,
    client: WikidataClient | None = None,
    settings: Settings | None = None,
) -> None:
    """Refresh linked items now: the given QIDs, those due, or all of them (`--all`)."""
    settings = settings or get_settings()
    reason = disabled_reason(settings)
    if reason is not None:
        raise SystemExit(reason)
    async with session_factory() as db, db.begin():
        run_ids: list[uuid.UUID] = []
        for qid in qids:
            root_id = await db.scalar(
                select(EntityExternalId.entity_id).where(
                    EntityExternalId.scheme == "wikidata", EntityExternalId.value == qid
                )
            )
            if root_id is None:
                raise SystemExit(f"{qid} is not linked to any entity")
            run_ids.append((await queue_fetch(db, root_id)).id)
        if not qids:
            run = WikidataRun(kind="refresh", force=everything)
            db.add(run)
            await db.flush()
            run_ids.append(run.id)
    if client is not None:
        for run_id in run_ids:
            await _work_wikidata_run(run_id, client, settings)
        return
    async with WikidataClient(settings, PostgresThrottle(settings)) as own:
        for run_id in run_ids:
            await _work_wikidata_run(run_id, own, settings)


async def print_wikidata_status(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    async with session_factory() as db:
        found = await wikidata_status(db, settings)
    print(
        f"wikidata: {'on' if found.enabled else 'off'}"
        + (f" ({found.reason})" if found.reason else "")
    )
    throttle = found.throttle
    if throttle.state == "paused":
        print(f"throttle: paused until {throttle.paused_until} ({throttle.pause_reason})")
    else:
        print(f"throttle: {throttle.state.replace('_', ' ')}")
    print(f"requests today: {throttle.requests_today} of {throttle.daily_budget}")
    print(
        f"links: {found.links}, roots with candidates: {found.open_candidates},"
        f" due for refresh: {found.due_refresh}"
    )
    if found.last_refresh is not None:
        print(f"last refresh: {_run_line(found.last_refresh)}")
    for run in found.runs:
        print(f"  {_run_line(run)}")


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
    if args.language:
        selection["language"] = args.language
    return selection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "create-user",
            "bootstrap-admin",
            "reset-password",
            "cleanup-article-storage",
            "rebuild-search",
            "search-index-status",
            "resume-search-rebuild",
            "reprocess-nlp",
            "nlp-status",
            "resume-nlp-reprocessing",
            "recluster",
            "authority",
            "wikidata",
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
    parser.add_argument(
        "--language",
        help="reprocess-nlp: narrow the selection to one detected language, e.g. el; "
        "authority export: export one language only",
    )
    parser.add_argument(
        "--file", type=Path, help="authority export|import: the JSON file (export: default stdout)"
    )
    parser.add_argument(
        "--qid", action="append", default=[], help="wikidata refresh: this linked item only"
    )
    args = parser.parse_args()
    if args.command == "wikidata":
        if args.username not in WIKIDATA_ACTIONS:
            parser.error("wikidata requires an action: " + ", ".join(WIKIDATA_ACTIONS))
        if args.username == "status":
            asyncio.run(print_wikidata_status())
            return
        if args.qid and args.all:
            parser.error("wikidata refresh takes --qid or --all, not both")
        asyncio.run(run_wikidata_refresh(qids=args.qid, everything=args.all))
        return
    if args.command == "authority":
        if args.username not in AUTHORITY_ACTIONS:
            parser.error("authority requires an action: " + ", ".join(AUTHORITY_ACTIONS))
        if args.username == "export":
            asyncio.run(run_authority_export(args.file, language=args.language))
        elif args.username == "import":
            if args.file is None:
                parser.error("authority import requires --file PATH")
            asyncio.run(run_authority_import(args.file, apply=args.apply))
        else:
            asyncio.run(run_seed_countries(apply=args.apply))
        return
    if args.command == "bootstrap-admin":
        if args.username:
            parser.error("bootstrap-admin does not accept an argument")
        asyncio.run(bootstrap_admin())
        return
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
        if args.language:
            parser.error("--language applies to reprocess-nlp only")
        if args.batch_size < 1 or args.batch_size > MAX_RECLUSTER_BATCH:
            parser.error(f"recluster batch size must be between 1 and {MAX_RECLUSTER_BATCH}")
        selection = _selection_from(args, parser)
        try:
            # Reject bad dates as an argument error rather than a traceback from the worker.
            validate_selection(selection)
        except ValueError as exc:
            parser.error(str(exc))
        asyncio.run(
            run_clustering_selection(
                selection=selection, apply=args.apply, batch_size=args.batch_size
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
        create_user(args.username, password=os.environ.get("NEWSINTEL_ADMIN_PASSWORD") or None)
        if args.command == "create-user"
        else reset_password(args.username)
    )
    asyncio.run(action)


if __name__ == "__main__":
    main()
