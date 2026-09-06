from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.enums import IncidentStatus
from app.schemas.incident import (
    IncidentCreate,
    IncidentCreateResponse,
    IncidentListItem,
    IncidentRead,
    IncidentUpdate,
    PossibleDuplicate,
    QuickCaptureIn,
)
from app.services.incidents import service as incidents_service
from app.services.retrieval.lexical import search_lexical

router = APIRouter(prefix="/incidents", tags=["incidents"])


async def _possible_duplicates(
    session: AsyncSession, incident_id: int, raw_problem: str
) -> list[PossibleDuplicate]:
    """Section 22: search before committing is the ideal, but at creation time we
    only just committed — so we search immediately after and surface hits as a
    non-blocking hint. Never auto-merges (Section 22: "Never automatically merge")."""
    hits = await search_lexical(session, raw_problem, limit=5)
    duplicates = []
    for hit in hits:
        if hit["incident_id"] == incident_id:
            continue
        incident = await incidents_service.get_incident(session, hit["incident_id"])
        if incident is not None:
            duplicates.append(PossibleDuplicate(incident_id=incident.id, title=incident.title))
    return duplicates[:3]


@router.post("", response_model=IncidentCreateResponse, status_code=201)
async def create_incident(
    data: IncidentCreate, session: AsyncSession = Depends(get_db)
) -> IncidentCreateResponse:
    incident = await incidents_service.create_incident(session, data)
    duplicates = await _possible_duplicates(session, incident.id, data.raw_problem)
    return IncidentCreateResponse(incident=incident, possible_duplicates=duplicates)


@router.post("/quick-capture", response_model=IncidentCreateResponse, status_code=201)
async def quick_capture(
    data: QuickCaptureIn, session: AsyncSession = Depends(get_db)
) -> IncidentCreateResponse:
    incident = await incidents_service.quick_capture(session, data)
    duplicates = await _possible_duplicates(session, incident.id, data.raw_text)
    return IncidentCreateResponse(incident=incident, possible_duplicates=duplicates)


@router.get("", response_model=list[IncidentListItem])
async def list_incidents(
    limit: int = 50,
    offset: int = 0,
    status: IncidentStatus | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[IncidentListItem]:
    incidents = await incidents_service.list_incidents(session, limit, offset, status)
    return incidents


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: int, session: AsyncSession = Depends(get_db)) -> IncidentRead:
    incident = await incidents_service.get_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: int, data: IncidentUpdate, session: AsyncSession = Depends(get_db)
) -> IncidentRead:
    incident = await incidents_service.update_incident(session, incident_id, data)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.post("/{incident_id}/archive", response_model=IncidentRead)
async def archive_incident(
    incident_id: int, session: AsyncSession = Depends(get_db)
) -> IncidentRead:
    incident = await incidents_service.archive_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident
