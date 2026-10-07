import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursors import cursor_or_400
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.entities import queries
from app.entities.schemas import (
    EntityArticlePage,
    EntityClusterPage,
    EntityDossierResponse,
    EntityRelationshipsResponse,
)
from app.nlp.models import Entity

router = APIRouter(tags=["entities"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]


async def _entity_or_404(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    entity = await queries.get_entity(db, entity_id)
    if entity is None:
        raise HTTPException(404, "Entity not found")
    return entity


@router.get("/entities/{entity_id}", response_model=EntityDossierResponse)
async def get_entity_dossier(
    entity_id: uuid.UUID, db: Db, _auth: Auth, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> EntityDossierResponse:
    entity = await _entity_or_404(db, entity_id)
    return await queries.dossier(db, entity, days)


@router.get("/entities/{entity_id}/articles", response_model=EntityArticlePage)
async def get_entity_articles(
    entity_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> EntityArticlePage:
    await _entity_or_404(db, entity_id)
    return await queries.articles(db, entity_id, limit, cursor_or_400(cursor))


@router.get("/entities/{entity_id}/clusters", response_model=EntityClusterPage)
async def get_entity_clusters(
    entity_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> EntityClusterPage:
    await _entity_or_404(db, entity_id)
    return await queries.clusters(db, entity_id, limit, cursor_or_400(cursor))


@router.get("/entities/{entity_id}/relationships", response_model=EntityRelationshipsResponse)
async def get_entity_relationships(
    entity_id: uuid.UUID, db: Db, _auth: Auth, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> EntityRelationshipsResponse:
    await _entity_or_404(db, entity_id)
    return await queries.relationships(db, entity_id, days)
