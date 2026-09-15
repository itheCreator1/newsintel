import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session
from app.auth.routes import current_session
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.graph.schemas import GraphResponse
from app.graph.service import MAX_NODES, focus_query
from app.graph.service import entity_graph as collect_entity_graph
from app.search.criteria import SearchCriteria, build_query, current_search_target, search_criteria
from app.search.elasticsearch import ElasticsearchAdapter, ElasticsearchUnavailable

router = APIRouter(tags=["graph"])
Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[Session, Depends(current_session)]
Config = Annotated[Settings, Depends(get_settings)]
Criteria = Annotated[SearchCriteria, Depends(search_criteria)]


@router.get("/graph/entities", response_model=GraphResponse)
async def entity_graph(
    db: Db,
    _auth: Auth,
    settings: Config,
    criteria: Criteria,
    focus_entity_id: uuid.UUID | None = None,
    nodes: Annotated[int, Query(ge=1, le=MAX_NODES)] = 30,
    min_edge_weight: Annotated[int, Query(ge=1)] = 2,
) -> GraphResponse:
    adapter = ElasticsearchAdapter(settings.elasticsearch_url)
    try:
        index_name, schema_version = await current_search_target(db, criteria)
        if schema_version < 2:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "search_upgrade_required",
                    "message": "Rebuild search to schema version 2 to use the entity graph",
                },
            )
        return await collect_entity_graph(
            db,
            adapter,
            index_name,
            query=focus_query(build_query(criteria, schema_version), focus_entity_id),
            entity_types=criteria.entity_types,
            nodes=nodes,
            min_edge_weight=min_edge_weight,
            focus_entity_id=focus_entity_id,
        )
    except ElasticsearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable") from exc
