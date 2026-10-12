import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.feeds.models import Article
from app.marks.models import ArticleMark
from app.marks.schemas import ArticleMarkResponse, ArticleMarkUpdate
from app.marks.service import mark_response, update_mark

router = APIRouter(tags=["marks"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


async def _article_or_404(db: AsyncSession, article_id: uuid.UUID) -> None:
    if await db.get(Article, article_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")


@router.get("/articles/{article_id}/mark", response_model=ArticleMarkResponse)
async def get_mark(article_id: uuid.UUID, db: Db, _auth: Auth) -> ArticleMarkResponse:
    await _article_or_404(db, article_id)
    return mark_response(article_id, await db.get(ArticleMark, article_id))


@router.put("/articles/{article_id}/mark", response_model=ArticleMarkResponse)
async def put_mark(
    article_id: uuid.UUID, change: ArticleMarkUpdate, db: Db, _session: Mutation
) -> ArticleMarkResponse:
    await _article_or_404(db, article_id)
    mark = await update_mark(db, article_id, change)
    response = mark_response(article_id, mark)
    await db.commit()
    return response
