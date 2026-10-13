import base64
import json
import uuid
from datetime import UTC, date, datetime, time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.cursors import cursor_or_400
from app.articles.models import Article, ArticleContent
from app.auth.dependencies import current_session, require_csrf
from app.auth.models import Session
from app.clustering.models import ArticleClusterState, StoryCluster, StoryClusterMember
from app.core.config import get_settings
from app.db.session import get_db
from app.feeds.discovery import FeedProbeError, probe_feed
from app.feeds.http import fetch_feed_http
from app.feeds.models import FETCH_FINISHED, Feed, FeedArticle, FeedFetch
from app.feeds.opml import OpmlEntry, OpmlError, parse_opml, render_opml
from app.feeds.schemas import (
    ArticleDetailResponse,
    ArticleListItem,
    ArticlePage,
    ArticleStoryCluster,
    CursorPage,
    FeedCandidate,
    FeedCreate,
    FeedImport,
    FeedImportResult,
    FeedResponse,
    FeedTagCount,
    FeedTestRequest,
    FeedTestResponse,
    FeedUpdate,
    FetchPage,
    OpmlPreview,
    OpmlPreviewItem,
    OpmlPreviewRequest,
    PollResponse,
    RelatedArticle,
)
from app.feeds.service import (
    QueueUnavailable,
    article_detail_response,
    article_response,
    encode_cursor,
    fetch_now,
    send_fetch,
)
from app.marks.models import ArticleMark
from app.marks.service import mark_response, marks_for
from app.nlp.models import ArticleLanguageAnnotation
from app.search.service import request_source_refresh

router = APIRouter(tags=["feeds"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


async def _active_feed(db: AsyncSession, feed_id: uuid.UUID, *, lock: bool = False) -> Feed:
    query = select(Feed).where(Feed.id == feed_id, Feed.retired_at.is_(None))
    feed = await db.scalar(query.with_for_update() if lock else query)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feed not found")
    return feed


FeedListStatus = Literal["active", "disabled", "failing", "retired"]
FeedSort = Literal["newest", "name"]


def _encode_name_cursor(name_key: str, item_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps([name_key, str(item_id)]).encode()).decode()


def _decode_name_cursor(cursor: str) -> tuple[str, uuid.UUID]:
    try:
        name_key, item_id = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return str(name_key), uuid.UUID(item_id)
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


def _fetch_error(category: str | None, message: str | None) -> str | None:
    return ": ".join(part for part in (category, message) if part) or None


@router.get("/feeds", response_model=CursorPage)
async def list_feeds(  # noqa: C901
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: FeedListStatus | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    sort: FeedSort = "newest",
) -> CursorPage:
    # The newest finished fetch per source, read through the (feed_id, started_at) index.
    last_fetch = (
        select(
            FeedFetch.status.label("status"),
            FeedFetch.error_category.label("error_category"),
            FeedFetch.error_message.label("error_message"),
            FeedFetch.completed_at.label("completed_at"),
        )
        .where(FeedFetch.feed_id == Feed.id, FeedFetch.status.in_(FETCH_FINISHED))
        .order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc())
        .limit(1)
        .lateral("last_fetch")
    )
    query = select(Feed, last_fetch).outerjoin(last_fetch, true())
    if status == "retired":
        query = query.where(Feed.retired_at.is_not(None))
    else:
        query = query.where(Feed.retired_at.is_(None))
    if status in ("active", "failing"):
        query = query.where(Feed.enabled.is_(True))
    if status == "disabled":
        query = query.where(Feed.enabled.is_(False))
    if status == "failing":
        query = query.where(last_fetch.c.status == "failed")
    if q and q.strip():
        term = q.strip()
        query = query.where(
            or_(
                Feed.name.icontains(term, autoescape=True),
                Feed.url.icontains(term, autoescape=True),
            )
        )
    if tag:
        query = query.where(Feed.tags.contains([tag]))

    name_key = func.lower(Feed.name)
    if sort == "name":
        query = query.order_by(name_key, Feed.id)
        if cursor:
            after_name, after_id = _decode_name_cursor(cursor)
            query = query.where(
                or_(name_key > after_name, and_(name_key == after_name, Feed.id > after_id))
            )
    else:
        query = query.order_by(Feed.created_at.desc(), Feed.id.desc())
        if cursor:
            created, item_id = cursor_or_400(cursor)
            query = query.where(
                or_(Feed.created_at < created, and_(Feed.created_at == created, Feed.id < item_id))
            )
    rows = list((await db.execute(query.limit(limit + 1))).all())
    items = [
        FeedResponse.model_validate(row.Feed).model_copy(
            update={
                "last_fetch_status": row.status,
                "last_fetch_error": _fetch_error(row.error_category, row.error_message)
                if row.status == "failed"
                else None,
                "last_fetch_at": row.completed_at,
            }
        )
        for row in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1].Feed
        next_cursor = (
            _encode_name_cursor(last.name.lower(), last.id)
            if sort == "name"
            else encode_cursor(last.created_at, last.id)
        )
    return CursorPage(items=items, next_cursor=next_cursor)


@router.get("/feeds/tags", response_model=list[FeedTagCount])
async def list_feed_tags(db: Db, _auth: Auth) -> list[FeedTagCount]:
    """Every tag on a source that is not retired, with how many sources carry it."""
    tags = (
        select(func.jsonb_array_elements_text(Feed.tags).label("tag"))
        .where(Feed.retired_at.is_(None))
        .subquery()
    )
    count = func.count().label("count")
    rows = await db.execute(
        select(tags.c.tag, count).group_by(tags.c.tag).order_by(count.desc(), tags.c.tag)
    )
    return [FeedTagCount(tag=row.tag, count=row.count) for row in rows]


async def _existing_by_url(db: AsyncSession, urls: list[str]) -> dict[str, uuid.UUID]:
    """Sources that are not retired, by the addresses given; a retired address may come back."""
    if not urls:
        return {}
    rows = await db.execute(
        select(Feed.url, Feed.id).where(Feed.url.in_(urls), Feed.retired_at.is_(None))
    )
    return {row.url: row.id for row in rows}


@router.get("/feeds/opml", response_class=Response)
async def export_opml(db: Db, _auth: Auth) -> Response:
    feeds = (
        await db.scalars(
            select(Feed).where(Feed.retired_at.is_(None)).order_by(func.lower(Feed.name), Feed.id)
        )
    ).all()
    entries = [
        OpmlEntry(feed.name, feed.url, list(feed.tags), feed.source_country, feed.expected_language)
        for feed in feeds
    ]
    return Response(
        render_opml(entries, title="newsintel sources"),
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="newsintel-sources.opml"'},
    )


@router.post("/feeds/opml/preview", response_model=OpmlPreview)
async def preview_opml(payload: OpmlPreviewRequest, db: Db, _mutation: Mutation) -> OpmlPreview:
    try:
        parsed = parse_opml(payload.opml.encode())
    except OpmlError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    existing = await _existing_by_url(db, [entry.url for entry in parsed.entries])
    return OpmlPreview(
        items=[
            OpmlPreviewItem(
                name=entry.name,
                url=entry.url,
                tags=entry.tags,
                source_country=entry.source_country,
                expected_language=entry.expected_language,
                existing_feed_id=existing.get(entry.url),
            )
            for entry in parsed.entries
        ],
        invalid=parsed.invalid,
    )


@router.post("/feeds/import", response_model=FeedImportResult)
async def import_feeds(payload: FeedImport, db: Db, _mutation: Mutation) -> FeedImportResult:
    """Adds each source whose address no source collects yet; the rest are skipped."""
    rows = [feed.model_dump(mode="json") for feed in payload.feeds]
    taken = set(await _existing_by_url(db, [row["url"] for row in rows]))
    created = 0
    for row in rows:
        if row["url"] in taken:
            continue
        taken.add(row["url"])
        db.add(Feed(**row))
        created += 1
    await db.commit()
    return FeedImportResult(created=created, skipped=len(rows) - created)


@router.post("/feeds/test", response_model=FeedTestResponse)
async def test_feed_url(payload: FeedTestRequest, db: Db, _mutation: Mutation) -> FeedTestResponse:
    settings = get_settings()
    given = str(payload.url)
    try:
        probe = await probe_feed(given, lambda url: fetch_feed_http(url, {}, settings))
    except FeedProbeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    existing = await _existing_by_url(db, [probe.url, given])
    return FeedTestResponse(
        url=probe.url,
        title=probe.title,
        entry_count=probe.entry_count,
        language=probe.language,
        latest_entry_at=probe.latest_entry_at,
        discovered_from=probe.discovered_from,
        candidates=[FeedCandidate(url=url, title=title) for url, title in probe.candidates],
        existing_feed_id=existing.get(probe.url) or existing.get(given),
    )


@router.post("/feeds", response_model=FeedResponse, status_code=status.HTTP_201_CREATED)
async def create_feed(payload: FeedCreate, db: Db, _mutation: Mutation) -> Feed:
    feed = Feed(**payload.model_dump(mode="json"))
    db.add(feed)
    await db.commit()
    await db.refresh(feed)
    return feed


@router.get("/feeds/{feed_id}", response_model=FeedResponse)
async def get_feed(feed_id: uuid.UUID, db: Db, _auth: Auth) -> Feed:
    return await _active_feed(db, feed_id)


@router.patch("/feeds/{feed_id}", response_model=FeedResponse)
async def update_feed(feed_id: uuid.UUID, payload: FeedUpdate, db: Db, _mutation: Mutation) -> Feed:
    feed = await _active_feed(db, feed_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    refresh_search = any(
        key in changes and getattr(feed, key) != changes[key] for key in ("name", "source_country")
    )
    for key, value in changes.items():
        setattr(feed, key, value)
    if refresh_search:
        await request_source_refresh(db, feed.id)
    await db.commit()
    await db.refresh(feed)
    return feed


@router.delete("/feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def retire_feed(feed_id: uuid.UUID, db: Db, _mutation: Mutation) -> Response:
    # Locked: clearing a claim loaded before the scheduler committed it would be a no-op.
    feed = await _active_feed(db, feed_id, lock=True)
    feed.retired_at = datetime.now(UTC)
    feed.enabled = False
    feed.claim_token = None
    feed.claim_expires_at = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/feeds/{feed_id}/poll", response_model=PollResponse, status_code=status.HTTP_202_ACCEPTED
)
async def poll_feed(feed_id: uuid.UUID, db: Db, _mutation: Mutation) -> PollResponse:
    try:
        claimed = await fetch_now(db, feed_id, send_fetch)
    except QueueUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Polling queue unavailable"
        ) from exc
    if claimed is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Feed is disabled or retired")
    fetch, reused = claimed
    return PollResponse(fetch_id=fetch.id, status=fetch.status, reused=reused)


@router.get("/feeds/{feed_id}/fetches", response_model=FetchPage)
async def list_fetches(
    feed_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> FetchPage:
    await _active_feed(db, feed_id)
    query = (
        select(FeedFetch)
        .where(FeedFetch.feed_id == feed_id)
        .order_by(FeedFetch.started_at.desc(), FeedFetch.id.desc())
    )
    if cursor:
        started, item_id = cursor_or_400(cursor)
        query = query.where(
            or_(
                FeedFetch.started_at < started,
                and_(FeedFetch.started_at == started, FeedFetch.id < item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = (
        encode_cursor(rows[limit - 1].started_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return FetchPage(items=rows[:limit], next_cursor=next_cursor)


@router.get("/articles", response_model=ArticlePage)
async def list_articles(
    db: Db,
    _auth: Auth,
    feed_id: uuid.UUID | None = None,
    unread: bool | None = None,
    starred: bool | None = None,
    language: Annotated[str | None, Query(max_length=16)] = None,
    has_text: bool | None = None,
    after: date | None = None,
    before: date | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ArticlePage:
    """Newest first; the dates are days of discovery in UTC, `before` not included."""
    query = (
        select(Article)
        .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        .order_by(Article.first_discovered_at.desc(), Article.id.desc())
    )
    if feed_id:
        query = query.where(
            select(FeedArticle.article_id)
            .where(FeedArticle.article_id == Article.id, FeedArticle.feed_id == feed_id)
            .exists()
        )
    if unread is not None:
        read = (
            select(ArticleMark.article_id)
            .where(ArticleMark.article_id == Article.id, ArticleMark.read_at.is_not(None))
            .exists()
        )
        query = query.where(~read if unread else read)
    if starred is not None:
        marked = (
            select(ArticleMark.article_id)
            .where(ArticleMark.article_id == Article.id, ArticleMark.starred.is_(True))
            .exists()
        )
        query = query.where(marked if starred else ~marked)
    if language:
        query = query.where(
            select(ArticleLanguageAnnotation.id)
            .where(
                ArticleLanguageAnnotation.article_id == Article.id,
                ArticleLanguageAnnotation.is_current.is_(True),
                ArticleLanguageAnnotation.language == language,
            )
            .exists()
        )
    if has_text is not None:
        text = (
            select(ArticleContent.article_id)
            .where(ArticleContent.article_id == Article.id)
            .exists()
        )
        query = query.where(text if has_text else ~text)
    if after:
        query = query.where(Article.first_discovered_at >= datetime.combine(after, time(), UTC))
    if before:
        query = query.where(Article.first_discovered_at < datetime.combine(before, time(), UTC))
    if cursor:
        discovered, item_id = cursor_or_400(cursor)
        query = query.where(
            or_(
                Article.first_discovered_at < discovered,
                and_(Article.first_discovered_at == discovered, Article.id < item_id),
            )
        )
    rows = list((await db.scalars(query.limit(limit + 1))).unique().all())
    next_cursor = (
        encode_cursor(rows[limit - 1].first_discovered_at, rows[limit - 1].id)
        if len(rows) > limit
        else None
    )
    page = rows[:limit]
    marks = await marks_for(db, [row.id for row in page])
    return ArticlePage(
        items=[
            ArticleListItem(
                **article_response(row).model_dump(exclude={"read", "starred"}),
                read=row.id in marks and marks[row.id].read_at is not None,
                starred=row.id in marks and marks[row.id].starred,
            )
            for row in page
        ],
        next_cursor=next_cursor,
    )


@router.get("/articles/{article_id}", response_model=ArticleDetailResponse)
async def get_article(article_id: uuid.UUID, db: Db, _auth: Auth) -> ArticleDetailResponse:
    article = await db.scalar(
        select(Article)
        .where(Article.id == article_id)
        .options(
            selectinload(Article.discoveries).selectinload(FeedArticle.feed),
            selectinload(Article.content),
            selectinload(Article.processing_jobs),
        )
    )
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    cluster_state = await db.scalar(
        select(ArticleClusterState).where(ArticleClusterState.article_id == article_id)
    )
    membership = await db.scalar(
        select(StoryClusterMember).where(StoryClusterMember.article_id == article_id)
    )
    story_cluster: ArticleStoryCluster | None = None
    related: list[RelatedArticle] = []
    if membership is not None:
        cluster = await db.get(StoryCluster, membership.cluster_id)
        if cluster is not None:
            story_cluster = ArticleStoryCluster(
                id=cluster.id,
                article_count=cluster.article_count,
                source_count=cluster.source_count,
            )
            effective_date = func.coalesce(Article.published_at, Article.first_discovered_at)
            related_rows = (
                await db.execute(
                    select(StoryClusterMember, Article, effective_date.label("effective_date"))
                    .join(Article, Article.id == StoryClusterMember.article_id)
                    .where(
                        StoryClusterMember.cluster_id == membership.cluster_id,
                        StoryClusterMember.article_id != article_id,
                    )
                    .order_by(StoryClusterMember.score.desc(), StoryClusterMember.article_id.asc())
                    .limit(5)
                )
            ).all()
            related = [
                RelatedArticle(
                    article_id=related_article.id,
                    title=related_article.title,
                    effective_date=related_effective_date,
                    score=related_member.score,
                )
                for related_member, related_article, related_effective_date in related_rows
            ]
    return article_detail_response(
        article,
        clustering_status=cluster_state.status if cluster_state is not None else None,
        story_cluster=story_cluster,
        related=related,
        mark=mark_response(article.id, await db.get(ArticleMark, article.id)),
    )
