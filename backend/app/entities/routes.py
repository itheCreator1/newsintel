import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursors import cursor_or_400
from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.entities import authority, catalogue, queries
from app.entities.schemas import (
    AuthorityHistoryItem,
    AuthorityHistoryPage,
    AuthorityNameResponse,
    AuthorityRootPage,
    AuthorityRootResponse,
    AuthoritySuggestionList,
    AuthoritySuggestionResponse,
    EntityArticlePage,
    EntityAuthorityResponse,
    EntityAuthorityRunResponse,
    EntityAuthorityUpdate,
    EntityClusterPage,
    EntityDossierResponse,
    EntityHistory,
    EntityHistoryItem,
    EntityMergeRequest,
    EntityRelationshipsResponse,
    EntityStatus,
    EntityVariantList,
    EntityVariantResponse,
)
from app.entities.suggestions import suggest
from app.nlp.models import Entity, EntityAuthorityRun

router = APIRouter(tags=["entities"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


async def _entity_or_404(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    """The entity an id stands for: a variant's id answers with its root."""
    entity = await queries.get_entity(db, entity_id)
    if entity is not None and entity.authority_id is not None:
        entity = await queries.get_entity(db, entity.authority_id)
    if entity is None:
        raise HTTPException(404, "Entity not found")
    return entity


def _authority_response(entity: Entity) -> EntityAuthorityResponse:
    return EntityAuthorityResponse(
        id=entity.id,
        display_name=entity.name,
        preferred_text=entity.preferred_text,
        status=entity.status,
        ambiguous=entity.ambiguous,
        note=entity.note,
        authority_id=entity.authority_id,
    )


def _run_response(run: EntityAuthorityRun) -> EntityAuthorityRunResponse:
    return EntityAuthorityRunResponse(
        id=run.id,
        kind=run.kind,
        status=run.status,
        entity_id=run.entity_id,
        root_id=uuid.UUID(json.loads(run.cursor or "{}")["root"]),
    )


def _refused(error: authority.AuthorityError) -> HTTPException:
    return HTTPException(error.status_code, error.detail)


@router.get("/entities/{entity_id}", response_model=EntityDossierResponse)
async def get_entity_dossier(
    entity_id: uuid.UUID, db: Db, _auth: Auth, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> EntityDossierResponse:
    entity = await _entity_or_404(db, entity_id)
    found = await queries.dossier(db, entity, days)
    if entity.id != entity_id:
        found.redirected_from = entity_id
    return found


@router.get("/entities/{entity_id}/articles", response_model=EntityArticlePage)
async def get_entity_articles(
    entity_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> EntityArticlePage:
    cursor_value = cursor_or_400(cursor)  # a malformed cursor is a 400 before any query
    entity = await _entity_or_404(db, entity_id)
    return await queries.articles(db, entity.id, limit, cursor_value)


@router.get("/entities/{entity_id}/clusters", response_model=EntityClusterPage)
async def get_entity_clusters(
    entity_id: uuid.UUID,
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> EntityClusterPage:
    cursor_value = cursor_or_400(cursor)  # a malformed cursor is a 400 before any query
    entity = await _entity_or_404(db, entity_id)
    return await queries.clusters(db, entity.id, limit, cursor_value)


@router.get("/entities/{entity_id}/relationships", response_model=EntityRelationshipsResponse)
async def get_entity_relationships(
    entity_id: uuid.UUID, db: Db, _auth: Auth, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> EntityRelationshipsResponse:
    entity = await _entity_or_404(db, entity_id)
    return await queries.relationships(db, entity.id, days)


@router.get("/entities/{entity_id}/variants", response_model=EntityVariantList)
async def get_entity_variants(entity_id: uuid.UUID, db: Db, _auth: Auth) -> EntityVariantList:
    root = await _entity_or_404(db, entity_id)
    return EntityVariantList(
        items=[
            EntityVariantResponse(
                id=item.id,
                display_name=item.display_text,
                normalized_text=item.normalized_text,
                language=item.language,
                entity_type=item.entity_type,
            )
            for item in await authority.variants(db, root.id)
        ]
    )


@router.get("/entities/{entity_id}/history", response_model=EntityHistory)
async def get_entity_history(entity_id: uuid.UUID, db: Db, _auth: Auth) -> EntityHistory:
    root = await _entity_or_404(db, entity_id)
    return EntityHistory(
        items=[
            EntityHistoryItem.model_validate(change, from_attributes=True)
            for change in await authority.history(db, root.id)
        ]
    )


@router.post(
    "/entities/{entity_id}/merge",
    response_model=EntityAuthorityRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def merge_entity(
    entity_id: uuid.UUID, payload: EntityMergeRequest, db: Db, _session: Mutation
) -> EntityAuthorityRunResponse:
    """Make this entity a variant of the target's root; its articles move over in batches."""
    try:
        run = await authority.merge(db, variant_id=entity_id, target_id=payload.target_id)
    except authority.AuthorityError as error:
        raise _refused(error) from None
    response = _run_response(run)
    await db.commit()
    return response


@router.post(
    "/entities/{entity_id}/split",
    response_model=EntityAuthorityRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def split_entity(
    entity_id: uuid.UUID, db: Db, _session: Mutation
) -> EntityAuthorityRunResponse:
    """Make a variant its own entity again; its mentions go back in batches."""
    try:
        run = await authority.split(db, entity_id)
    except authority.AuthorityError as error:
        raise _refused(error) from None
    response = _run_response(run)
    await db.commit()
    return response


@router.patch("/entities/{entity_id}", response_model=EntityAuthorityResponse)
async def update_entity(
    entity_id: uuid.UUID, payload: EntityAuthorityUpdate, db: Db, _session: Mutation
) -> EntityAuthorityResponse:
    """The preferred name and status belong to a root; any name can be ambiguous or noted."""
    if await queries.get_entity(db, entity_id) is None:
        raise HTTPException(404, "Entity not found")
    sent = payload.model_fields_set
    try:
        if "preferred_text" in sent:
            await authority.rename(db, entity_id, payload.preferred_text)
        if "status" in sent and payload.status is not None:
            await authority.set_status(db, entity_id, payload.status)
        if "ambiguous" in sent and payload.ambiguous is not None:
            await authority.set_ambiguous(db, entity_id, payload.ambiguous)
        if "note" in sent:
            await authority.set_note(db, entity_id, payload.note)
    except authority.AuthorityError as error:
        raise _refused(error) from None
    entity = await queries.get_entity(db, entity_id)
    assert entity is not None
    response = _authority_response(entity)
    await db.commit()
    return response


@router.post("/entities/{entity_id}/distinct/{other_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_entity_distinct(
    entity_id: uuid.UUID, other_id: uuid.UUID, db: Db, _session: Mutation
) -> Response:
    """Record that two entities are different, so neither is merged into the other."""
    try:
        await authority.add_distinct(db, entity_id, other_id)
    except authority.AuthorityError as error:
        raise _refused(error) from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/entities/{entity_id}/distinct/{other_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_entity_distinct(
    entity_id: uuid.UUID, other_id: uuid.UUID, db: Db, _session: Mutation
) -> Response:
    try:
        await authority.remove_distinct(db, entity_id, other_id)
    except authority.AuthorityError as error:
        raise _refused(error) from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/authorities", response_model=AuthorityRootPage)
async def list_authorities(
    db: Db,
    _auth: Auth,
    language: Annotated[str | None, Query(max_length=16)] = None,
    status_filter: Annotated[EntityStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuthorityRootPage:
    """The authority file: every root by name; `q` finds one by any of its names."""
    try:
        after = None if cursor is None else catalogue.decode_name_cursor(cursor)
    except ValueError:
        raise HTTPException(400, "Invalid cursor") from None
    page, next_cursor = await catalogue.roots(
        db, language=language, status=status_filter, q=q, limit=limit, cursor=after
    )
    return AuthorityRootPage(
        items=[
            AuthorityRootResponse(
                id=entity.id,
                display_name=entity.name,
                entity_type=entity.entity_type,
                language=entity.language,
                status=entity.status,
                ambiguous=entity.ambiguous,
                variant_count=count,
            )
            for entity, count in page
        ],
        next_cursor=next_cursor,
    )


@router.get("/authorities/suggestions", response_model=AuthoritySuggestionList)
async def list_authority_suggestions(
    db: Db,
    _auth: Auth,
    language: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuthoritySuggestionList:
    """Maybe the same? Approve with a merge of the variant, reject with a distinct pair."""
    found = await suggest(db, language=language, limit=limit)
    return AuthoritySuggestionList(
        items=[
            AuthoritySuggestionResponse(
                root=AuthorityNameResponse(
                    id=item.root.id,
                    display_name=item.root.name,
                    entity_type=item.root.entity_type,
                    article_count=item.root_articles,
                ),
                variant=AuthorityNameResponse(
                    id=item.variant.id,
                    display_name=item.variant.name,
                    entity_type=item.variant.entity_type,
                    article_count=item.variant_articles,
                ),
                score=item.score,
                reasons=item.reasons,
                shared_articles=item.shared_articles,
            )
            for item in found
        ]
    )


@router.get("/authorities/history", response_model=AuthorityHistoryPage)
async def list_authority_history(
    db: Db,
    _auth: Auth,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuthorityHistoryPage:
    """Every change to the authority file, newest first."""
    after = cursor_or_400(cursor)  # a malformed cursor is a 400 before any query
    page, next_cursor = await catalogue.recent_changes(db, limit=limit, cursor=after)
    return AuthorityHistoryPage(
        items=[
            AuthorityHistoryItem.model_validate(
                {
                    **EntityHistoryItem.model_validate(change, from_attributes=True).model_dump(),
                    "entity_name": name,
                    "other_name": other_name,
                }
            )
            for change, name, other_name in page
        ],
        next_cursor=next_cursor,
    )
