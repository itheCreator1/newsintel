import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.session import session_factory
from app.feeds.models import Article
from app.nlp.models import NlpJob, NlpReprocessingRun
from app.nlp.service import PROCESSORS, request_article_nlp


def _validate_processors(processor_names: tuple[str, ...]) -> None:
    unknown = sorted(set(processor_names) - set(PROCESSORS))
    if not processor_names or unknown:
        raise ValueError(f"invalid NLP processors: {', '.join(unknown) or 'none'}")


async def create_reprocessing_run(
    *, processor_names: tuple[str, ...], selection: dict[str, object]
) -> uuid.UUID:
    _validate_processors(processor_names)
    if not any(key in selection for key in ("article_ids", "from_date", "to_date", "all")):
        raise ValueError("reprocessing requires article IDs, a UTC date range, or explicit all")
    async with session_factory() as db, db.begin():
        run = NlpReprocessingRun(
            status="scanning",
            processor_names=list(processor_names),
            selection=selection,
        )
        db.add(run)
        await db.flush()
        return run.id


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("reprocessing dates must include a UTC offset")
    return parsed.astimezone(UTC)


async def count_selection(selection: dict[str, object]) -> int:
    async with session_factory() as db:
        query = select(func.count()).select_from(Article)
        article_ids = selection.get("article_ids")
        if isinstance(article_ids, list):
            query = query.where(Article.id.in_([uuid.UUID(str(value)) for value in article_ids]))
        from_date = _parse_datetime(selection.get("from_date"))
        to_date = _parse_datetime(selection.get("to_date"))
        if from_date:
            query = query.where(Article.first_discovered_at >= from_date)
        if to_date:
            query = query.where(Article.first_discovered_at < to_date)
        return int(await db.scalar(query) or 0)


async def scan_reprocessing(run_id: uuid.UUID, *, batch_size: int = 100) -> int:
    if batch_size < 1 or batch_size > 100:
        raise ValueError("NLP reprocessing batch size must be between 1 and 100")
    async with session_factory() as db, db.begin():
        run = await db.scalar(
            select(NlpReprocessingRun).where(NlpReprocessingRun.id == run_id).with_for_update()
        )
        if run is None:
            raise LookupError("NLP reprocessing run not found")
        if run.status == "succeeded":
            return 0
        query = select(Article.id).order_by(Article.id).limit(batch_size)
        if run.article_cursor:
            query = query.where(Article.id > run.article_cursor)
        article_ids = run.selection.get("article_ids")
        if isinstance(article_ids, list):
            query = query.where(Article.id.in_([uuid.UUID(str(value)) for value in article_ids]))
        from_date = _parse_datetime(run.selection.get("from_date"))
        to_date = _parse_datetime(run.selection.get("to_date"))
        if from_date:
            query = query.where(Article.first_discovered_at >= from_date)
        if to_date:
            query = query.where(Article.first_discovered_at < to_date)
        ids = list((await db.scalars(query)).all())
        processors = tuple(str(name) for name in run.processor_names)
        _validate_processors(processors)
        for article_id in ids:
            run.enqueued_count += await request_article_nlp(
                db, article_id, processor_names=processors, force=True
            )
        run.scanned_count += len(ids)
        if ids:
            run.article_cursor = ids[-1]
        if len(ids) < batch_size:
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
        return len(ids)


async def reprocessing_status() -> list[dict[str, object]]:
    async with session_factory() as db:
        runs = list(
            (
                await db.scalars(
                    select(NlpReprocessingRun)
                    .order_by(NlpReprocessingRun.created_at.desc())
                    .limit(20)
                )
            ).all()
        )
        job_count_rows = (
            await db.execute(select(NlpJob.status, func.count()).group_by(NlpJob.status))
        ).tuples()
        job_counts: dict[str, int] = {
            str(status): int(count) for status, count in job_count_rows
        }
    return [
        {
            "id": str(run.id),
            "status": run.status,
            "scanned": run.scanned_count,
            "enqueued": run.enqueued_count,
            "processors": run.processor_names,
            "jobs": job_counts,
        }
        for run in runs
    ]
