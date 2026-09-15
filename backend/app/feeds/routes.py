import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.clustering.models import ArticleClusterState, StoryCluster, StoryClusterMember
from app.db.session import get_db
from app.feeds.models import Article, Feed, FeedArticle, FeedFetch
from app.feeds.schemas import (
    ArticleDetailResponse,
    ArticlePage,
    ArticleStoryCluster,
    CursorPage,
    FeedCreate,
    FeedResponse,
    FeedUpdate,
    FetchPage,
    PollResponse,
    RelatedArticle,
)
from app.feeds.service import (
    article_detail_response,
    article_response,
    claim_feed,
    decode_cursor,
    encode_cursor,
)
from app.search.service import request_source_refresh

router = APIRouter(tags=["feeds"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


async def _active_feed(db: AsyncSession, feed_id: uuid.UUID) -> Feed:
    feed = await db.scalar(select(Feed).where(Feed.id == feed_id, Feed.retired_at.is_(None)))
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feed not found")
    return feed


@router.get("/feeds", response_model=CursorPage)
async def list_feeds(
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> CursorPage:
    query = (
        select(Feed)
        .where(Feed.retired_at.is_(None))
        .order_by(Feed.created_at.desc(), Feed.id.desc())
    )
    if cursor:
        created, item_id = decode_cursor(cursor)
        query = query.where(
            or_(Feed.created_at < created, and_(Feed.created_at == created, Feed.id < item_id))
        )
    rows = list((await db.scalars(query.limit(limit + 1))).all())
    next_cursor = (
        encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    )
    return CursorPage(items=rows[:limit], next_cursor=next_cursor)


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
    feed = await _active_feed(db, feed_id)
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
    claimed = await claim_feed(db, feed_id)
    if claimed is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Feed is disabled or retired")
    fetch, reused = claimed
    if not reused:
        try:
            from app.jobs.ingestion import ingest_feed

            ingest_feed.send(str(feed_id), fetch.claim_token)
        except Exception as exc:
            fetch.status = "failed"
            fetch.error_category = "queue"
            fetch.error_message = str(exc)[:1000]
            fetch.completed_at = datetime.now(UTC)
            feed = await db.get(Feed, feed_id)
            if feed and feed.claim_token == fetch.claim_token:
                feed.claim_token = None
                feed.claim_expires_at = None
            await db.commit()
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Polling queue unavailable"
            ) from exc
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
        started, item_id = decode_cursor(cursor)
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
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ArticlePage:
    query = (
        select(Article)
        .options(selectinload(Article.discoveries).selectinload(FeedArticle.feed))
        .order_by(Article.first_discovered_at.desc(), Article.id.desc())
    )
    if feed_id:
        query = query.join(FeedArticle).where(FeedArticle.feed_id == feed_id)
    if cursor:
        discovered, item_id = decode_cursor(cursor)
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
    return ArticlePage(
        items=[article_response(row) for row in rows[:limit]], next_cursor=next_cursor
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
                    .order_by(
                        StoryClusterMember.score.desc(), StoryClusterMember.article_id.asc()
                    )
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
    )
