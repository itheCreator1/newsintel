import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.wikidata import links
from app.wikidata.schemas import WikidataItemResponse, WikidataLinkRequest, WikidataLinkResponse

router = APIRouter(tags=["wikidata"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


def _refused(error: links.LinkError) -> HTTPException:
    return HTTPException(error.status_code, error.body())


def _response(found: links.LinkState) -> WikidataLinkResponse:
    return WikidataLinkResponse(
        entity_id=str(found.root.id),
        qid=found.qid,
        identifiers=found.identifiers,
        item=WikidataItemResponse.model_validate(found.item, from_attributes=True)
        if found.item is not None
        else None,
        fetch_pending=found.fetch_pending,
    )


@router.get("/entities/{entity_id}/wikidata", response_model=WikidataLinkResponse)
async def get_wikidata_link(entity_id: uuid.UUID, db: Db, _auth: Auth) -> WikidataLinkResponse:
    """The root's Wikidata link, identifiers and cached item; read locally, never from Wikidata."""
    try:
        return _response(await links.state(db, entity_id))
    except links.LinkError as error:
        raise _refused(error) from None


@router.post("/entities/{entity_id}/wikidata", response_model=WikidataLinkResponse)
async def link_wikidata(
    entity_id: uuid.UUID, payload: WikidataLinkRequest, db: Db, _session: Mutation
) -> WikidataLinkResponse:
    """Link the root to a Wikidata item. A QID another root holds is refused with that root."""
    try:
        found = await links.link(db, entity_id, payload.qid)
    except links.LinkError as error:
        raise _refused(error) from None
    response = _response(found)
    await db.commit()
    return response


@router.delete("/entities/{entity_id}/wikidata", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_wikidata(entity_id: uuid.UUID, db: Db, _session: Mutation) -> Response:
    """Remove the link and the identifiers read off the item; the names it brought stay."""
    try:
        await links.unlink(db, entity_id)
    except links.LinkError as error:
        raise _refused(error) from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
