from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.enums import IncidentRelationType
from app.schemas.relationship import (
    RelatedIncident,
    RelationCreate,
    RelationRead,
    SuggestedRelation,
)
from app.services.incidents import service as incidents_service
from app.services.knowledge_graph import service as kg_service

router = APIRouter(prefix="/incidents/{incident_id}", tags=["knowledge-graph"])


@router.post("/relations", response_model=RelationRead, status_code=201)
async def create_relation(
    incident_id: int, data: RelationCreate, session: AsyncSession = Depends(get_db)
) -> RelationRead:
    if await incidents_service.get_incident(session, incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if await incidents_service.get_incident(session, data.target_incident_id) is None:
        raise HTTPException(status_code=404, detail="Target incident not found")
    if incident_id == data.target_incident_id:
        raise HTTPException(status_code=400, detail="An incident cannot relate to itself")

    return await kg_service.create_relation(
        session,
        source_incident_id=incident_id,
        target_incident_id=data.target_incident_id,
        relation_type=data.relation_type,
        note=data.note,
    )


@router.get("/relations", response_model=list[RelationRead])
async def list_relations(
    incident_id: int, session: AsyncSession = Depends(get_db)
) -> list[RelationRead]:
    return await kg_service.list_relations(session, incident_id)


@router.delete("/relations/{relation_id}", status_code=204)
async def delete_relation(
    incident_id: int, relation_id: int, session: AsyncSession = Depends(get_db)
) -> None:
    if not await kg_service.delete_relation(session, relation_id):
        raise HTTPException(status_code=404, detail="Relation not found")


@router.get("/related", response_model=list[RelatedIncident])
async def get_related_incidents(
    incident_id: int,
    max_depth: int = 2,
    relation_type: IncidentRelationType | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[RelatedIncident]:
    return await kg_service.get_related_incidents(session, incident_id, max_depth, relation_type)


@router.get("/relations/suggested", response_model=list[SuggestedRelation])
async def suggest_relations(
    incident_id: int, limit: int = 5, session: AsyncSession = Depends(get_db)
) -> list[SuggestedRelation]:
    if await incidents_service.get_incident(session, incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return await kg_service.suggest_relations(session, incident_id, limit=limit)
