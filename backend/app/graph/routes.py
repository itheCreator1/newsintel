import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.entities.resolver import entity_group
from app.graph.schemas import EdgeEvidenceResponse, GraphResponse
from app.graph.service import (
    MAX_EVIDENCE,
    MAX_EXPANDED,
    MAX_NODES,
    decode_after,
    focus_query,
    recent_since,
    stated_links,
)
from app.graph.service import edge_evidence as collect_edge_evidence
from app.graph.service import entity_graph as collect_entity_graph
from app.nlp.models import Entity
from app.search.criteria import SearchCriteria, build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

router = APIRouter(tags=["graph"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Criteria = Annotated[SearchCriteria, Depends(search_criteria)]


async def _focus(
    db: AsyncSession, focus_entity_id: uuid.UUID | None
) -> tuple[uuid.UUID | None, list[str] | None]:
    """A merged id focuses its root under all its ids; an unknown one still narrows to nothing."""
    if focus_entity_id is None:
        return None, None
    group = await entity_group(db, focus_entity_id)
    return (focus_entity_id, None) if group is None else group


@router.get("/graph/entities", response_model=GraphResponse)
async def entity_graph(
    db: Db,
    _auth: Auth,
    settings: Config,
    criteria: Criteria,
    focus_entity_id: uuid.UUID | None = None,
    nodes: Annotated[int, Query(ge=1, le=MAX_NODES)] = 30,
    min_edge_weight: Annotated[int, Query(ge=1)] = 2,
    expand: Annotated[list[uuid.UUID] | None, Query(max_length=MAX_EXPANDED)] = None,
    stated: bool = False,
) -> GraphResponse:
    focus_entity_id, focus_ids = await _focus(db, focus_entity_id)
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    try:
        index_name, schema_version = await current_search_target(db, criteria, minimum=2)
        graph = await collect_entity_graph(
            db,
            adapter,
            index_name,
            query=focus_query(build_query(criteria, schema_version), focus_entity_id, focus_ids),
            entity_types=criteria.entity_types,
            nodes=nodes,
            min_edge_weight=min_edge_weight,
            focus_entity_id=focus_entity_id,
            expand=expand,
            since=recent_since(criteria.start, criteria.end, datetime.now(UTC)),
            focus_ids=focus_ids,
        )
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
    if not stated:
        return graph
    # Stated links are drawn beside co-occurrence and never change its edges or weights.
    drawn = [str(node.id) for node in graph.nodes]
    return graph.model_copy(update={"stated_edges": await stated_links(db, drawn)})


@router.get("/graph/edges/evidence", response_model=EdgeEvidenceResponse)
async def edge_evidence(
    db: Db,
    _auth: Auth,
    settings: Config,
    criteria: Criteria,
    source: uuid.UUID,
    target: uuid.UUID,
    focus_entity_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_EVIDENCE)] = 10,
    cursor: str | None = None,
) -> EdgeEvidenceResponse:
    """The articles and stories behind one graph edge, under the same filters as the graph."""
    if source == target:
        raise HTTPException(422, "An edge joins two entities")
    try:
        after = decode_after(cursor) if cursor else None
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid cursor") from None
    source_group, target_group = await entity_group(db, source), await entity_group(db, target)
    if source_group is None or target_group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found")
    (source_root, source_ids), (target_root, target_ids) = source_group, target_group
    if source_root == target_root:
        raise HTTPException(422, "An edge joins two entities")
    entities = [await db.get(Entity, source_root), await db.get(Entity, target_root)]
    if entities[0] is None or entities[1] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entity not found")
    focus_entity_id, focus_ids = await _focus(db, focus_entity_id)
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    try:
        index_name, schema_version = await current_search_target(db, criteria, minimum=2)
        return await collect_edge_evidence(
            db,
            adapter,
            index_name,
            query=focus_query(build_query(criteria, schema_version), focus_entity_id, focus_ids),
            source=entities[0],
            target=entities[1],
            limit=limit,
            after=after,
            source_ids=source_ids,
            target_ids=target_ids,
        )
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
