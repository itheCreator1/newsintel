import base64
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_csrf
from app.auth.models import Session
from app.auth.routes import current_session
from app.db.session import get_db
from app.wikidata import candidates, links, runs
from app.wikidata.models import WikidataCandidate, WikidataItem, WikidataRun
from app.wikidata.names import Name
from app.wikidata.schemas import (
    WikidataApproveResponse,
    WikidataCandidateResponse,
    WikidataHolder,
    WikidataItemResponse,
    WikidataLinkRequest,
    WikidataLinkResponse,
    WikidataName,
    WikidataNameResponse,
    WikidataNamesRequest,
    WikidataReviewItem,
    WikidataReviewPage,
    WikidataRunResponse,
    WikidataSkipped,
)

router = APIRouter(tags=["wikidata"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Mutation = Annotated[Session, Depends(require_csrf)]


def _refused(error: links.LinkError) -> HTTPException:
    return HTTPException(error.status_code, error.body())


def _candidate(
    row: WikidataCandidate, item: WikidataItem | None, language: str
) -> WikidataCandidateResponse:
    return WikidataCandidateResponse(
        qid=row.qid,
        score=row.score,
        reasons=list(row.reasons),
        exact=row.exact,
        label=candidates.label_for(item, language),
        description=candidates.description_for(item, language),
        sitelinks=item.sitelinks if item is not None else 0,
    )


async def _response(db: AsyncSession, found: links.LinkState) -> WikidataLinkResponse:
    root = found.root
    offered = [] if found.qid else await candidates.for_root(db, root.id)
    return WikidataLinkResponse(
        entity_id=str(found.root.id),
        qid=found.qid,
        identifiers=found.identifiers,
        item=WikidataItemResponse.model_validate(found.item, from_attributes=True)
        if found.item is not None
        else None,
        fetch_pending=found.fetch_pending,
        names=[
            WikidataNameResponse(
                language=state.name.language,
                text=state.name.text,
                kind=state.name.kind,
                status=state.status,
                entity_id=str(state.entity_id) if state.entity_id else None,
            )
            for state in found.names
        ],
        candidates=[_candidate(row, item, root.language) for row, item in offered],
        search_pending=await runs.search_pending(db, root.id),
        redirect_holder=WikidataHolder(
            entity_id=str(found.redirect_holder.id), display_name=found.redirect_holder.name
        )
        if found.redirect_holder is not None
        else None,
    )


def _names(values: list[WikidataName]) -> list[Name]:
    return [Name(value.language, value.text, "alias") for value in values]


@router.get("/entities/{entity_id}/wikidata", response_model=WikidataLinkResponse)
async def get_wikidata_link(entity_id: uuid.UUID, db: Db, _auth: Auth) -> WikidataLinkResponse:
    """The root's Wikidata link, identifiers and cached item; read locally, never from Wikidata."""
    try:
        return await _response(db, await links.state(db, entity_id))
    except links.LinkError as error:
        raise _refused(error) from None


@router.post("/entities/{entity_id}/wikidata", response_model=WikidataLinkResponse)
async def link_wikidata(
    entity_id: uuid.UUID, payload: WikidataLinkRequest, db: Db, _session: Mutation
) -> WikidataLinkResponse:
    """Link the root to a Wikidata item. A QID another root holds is refused with that root."""
    try:
        found = await links.link(db, entity_id, payload.qid, _names(payload.aliases))
    except links.LinkError as error:
        raise _refused(error) from None
    response = await _response(db, found)
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


@router.post("/entities/{entity_id}/wikidata/names", response_model=WikidataLinkResponse)
async def add_wikidata_names(
    entity_id: uuid.UUID, payload: WikidataNamesRequest, db: Db, _session: Mutation
) -> WikidataLinkResponse:
    """Add more of the linked item's names as variants: only names the item has."""
    try:
        found = await links.add_item_names(db, entity_id, _names(payload.names))
    except links.LinkError as error:
        raise _refused(error) from None
    response = await _response(db, found)
    await db.commit()
    return response


def run_response(run: WikidataRun) -> WikidataRunResponse:
    return WikidataRunResponse(
        id=str(run.id),
        kind=run.kind,
        status=run.status,
        entity_id=str(run.entity_id) if run.entity_id else None,
        checked=run.checked,
        changed=run.changed,
        redirected=run.redirected,
        missing=run.missing,
        errors=run.errors,
        requests=run.requests,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post(
    "/entities/{entity_id}/wikidata/search",
    response_model=WikidataRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def search_wikidata(entity_id: uuid.UUID, db: Db, _session: Mutation) -> WikidataRunResponse:
    """Queue a search for candidates; a worker asks Wikidata, never this request."""
    try:
        run = await runs.request_search(db, entity_id)
    except links.LinkError as error:
        raise _refused(error) from None
    response = run_response(run)
    await db.commit()
    return response


@router.post(
    "/entities/{entity_id}/wikidata/candidates/{qid}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def dismiss_wikidata_candidate(
    entity_id: uuid.UUID, qid: str, db: Db, _session: Mutation
) -> Response:
    """Not this item: it is never suggested for this root again."""
    if not await candidates.dismiss(db, entity_id, qid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such candidate")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _encode(position: tuple[float, uuid.UUID, str]) -> str:
    score, entity_id, qid = position
    return base64.urlsafe_b64encode(json.dumps([score, str(entity_id), qid]).encode()).decode()


def _decode(cursor: str) -> tuple[float, uuid.UUID, str]:
    try:
        score, entity_id, qid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return float(score), uuid.UUID(entity_id), str(qid)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid cursor") from exc


@router.get("/wikidata/candidates", response_model=WikidataReviewPage)
async def review_wikidata_candidates(
    db: Db,
    _auth: Auth,
    min_score: Annotated[float, Query(ge=-1, le=2)] = 0.5,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> WikidataReviewPage:
    """Open candidates of unlinked roots, best first: the Authority file's review tab."""
    rows, following = await candidates.review_queue(
        db, min_score=min_score, limit=limit, after=_decode(cursor) if cursor else None
    )
    return WikidataReviewPage(
        items=[
            WikidataReviewItem(
                **_candidate(row, item, entity.language).model_dump(),
                entity_id=str(entity.id),
                display_name=entity.name,
                entity_type=entity.entity_type,
                language=entity.language,
            )
            for row, entity, item in rows
        ],
        next_cursor=_encode(following) if following else None,
    )


@router.post("/wikidata/candidates/approve-exact", response_model=WikidataApproveResponse)
async def approve_exact_wikidata_candidates(db: Db, _session: Mutation) -> WikidataApproveResponse:
    """Link every root whose one exact label of the right type is clear (decided 2026-10-08).

    Each link is the same as the user's own: labels become variants, aliases stay unticked.
    A refusal (the item went to another root meanwhile) skips that root and says why.
    """
    linked = 0
    skipped: list[WikidataSkipped] = []
    for entity_id, qid in await candidates.exact_candidates(db):
        try:
            async with db.begin_nested():
                await links.link(db, entity_id, qid)
            linked += 1
        except links.LinkError as error:
            skipped.append(WikidataSkipped(entity_id=str(entity_id), qid=qid, message=error.detail))
    await db.commit()
    return WikidataApproveResponse(linked=linked, skipped=skipped)


@router.post(
    "/entities/{entity_id}/wikidata/refresh",
    response_model=WikidataRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_wikidata(entity_id: uuid.UUID, db: Db, _session: Mutation) -> WikidataRunResponse:
    """Queue a fetch of the linked item: again in full if its revision changed."""
    try:
        run = await runs.request_refresh(db, entity_id)
    except links.LinkError as error:
        raise _refused(error) from None
    response = run_response(run)
    await db.commit()
    return response
