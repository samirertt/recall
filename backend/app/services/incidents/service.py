"""Incident CRUD (Phase 3/5). One implementation shared by the REST API and, later,
the MCP server (docs/ARCHITECTURE.md § 2) — no business logic duplicated behind MCP.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import IncidentStatus
from app.models.environment import Environment
from app.models.incident import Attempt, Incident
from app.models.reference import Project, Tag, Technology
from app.models.relationships import IncidentProject, IncidentTag, IncidentTechnology
from app.schemas.incident import IncidentCreate, IncidentUpdate, QuickCaptureIn
from app.services.ai.service import enrich_incident
from app.services.embeddings.service import embed_incident

logger = logging.getLogger(__name__)

_INCIDENT_LOAD_OPTIONS = (
    selectinload(Incident.environment),
    selectinload(Incident.attempts),
    selectinload(Incident.project_links),
    selectinload(Incident.technology_links),
    selectinload(Incident.tag_links),
)


async def create_incident(session: AsyncSession, data: IncidentCreate) -> Incident:
    """Section 18: zero-friction capture. The raw save always succeeds synchronously
    with no AI/embedding/attachment dependency (docs/ARCHITECTURE.md § 3); enrichment
    and embedding run best-effort immediately after, never blocking or failing this
    call. Title starts null — `needs_ai_review` stays true until a real (non-
    heuristic) provider looks at it, and the frontend renders a null title as
    "Incident #<id>" so this is never a broken/empty-looking state."""
    incident = Incident(
        raw_problem=data.raw_problem,
        raw_solution=data.raw_solution,
        status=IncidentStatus.solved if data.raw_solution else IncidentStatus.unresolved,
        needs_ai_review=True,
    )
    session.add(incident)
    await session.commit()
    await _try_enrich(session, incident)
    await _try_embed(session, incident)
    return await get_incident(session, incident.id)


async def quick_capture(session: AsyncSession, data: QuickCaptureIn) -> Incident:
    """Section 19: save immediately; structuring happens via best-effort enrichment."""
    return await create_incident(session, IncidentCreate(raw_problem=data.raw_text))


async def _try_enrich(session: AsyncSession, incident: Incident) -> None:
    """AI enrichment (Phase 10) is best-effort and must never fail/block a capture or
    edit (docs/ARCHITECTURE.md § 10) — errors never propagate, but are logged rather
    than silently swallowed (Section 87)."""
    try:
        await enrich_incident(session, incident)
    except Exception:
        logger.exception("AI enrichment failed for incident %s; continuing without it", incident.id)


async def _try_embed(session: AsyncSession, incident: Incident) -> None:
    """Embedding is best-effort and must never fail/block a capture or edit
    (docs/ARCHITECTURE.md § 10 degradation matrix) — errors never propagate, but are
    logged rather than silently swallowed (Section 87: don't hide failures)."""
    try:
        await embed_incident(session, incident)
    except Exception:
        logger.exception("Embedding failed for incident %s; continuing without it", incident.id)


async def get_incident(session: AsyncSession, incident_id: int) -> Incident | None:
    result = await session.execute(
        select(Incident).where(Incident.id == incident_id).options(*_INCIDENT_LOAD_OPTIONS)
    )
    return result.scalar_one_or_none()


async def list_incidents(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    status: IncidentStatus | None = None,
) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(Incident.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _get_or_create(session: AsyncSession, model, name: str):
    result = await session.execute(select(model).where(model.name == name))
    obj = result.scalar_one_or_none()
    if obj is None:
        obj = model(name=name)
        session.add(obj)
        await session.flush()
    return obj


async def update_incident(
    session: AsyncSession, incident_id: int, data: IncidentUpdate
) -> Incident | None:
    incident = await get_incident(session, incident_id)
    if incident is None:
        return None

    plain_fields = data.model_dump(
        exclude_unset=True,
        exclude={"environment", "attempts", "project_names", "technology_names", "tag_names"},
    )
    for field, value in plain_fields.items():
        setattr(incident, field, value)

    if data.environment is not None:
        env_data = data.environment.model_dump()
        if incident.environment is None:
            incident.environment = Environment(**env_data)
        else:
            for field, value in env_data.items():
                setattr(incident.environment, field, value)

    if data.attempts is not None:
        incident.attempts.clear()
        await session.flush()
        incident.attempts = [
            Attempt(order=i, **attempt.model_dump()) for i, attempt in enumerate(data.attempts)
        ]

    if data.project_names is not None:
        incident.project_links.clear()
        await session.flush()
        for name in data.project_names:
            project = await _get_or_create(session, Project, name)
            incident.project_links.append(IncidentProject(project_id=project.id))

    if data.technology_names is not None:
        incident.technology_links.clear()
        await session.flush()
        for name in data.technology_names:
            technology = await _get_or_create(session, Technology, name)
            incident.technology_links.append(IncidentTechnology(technology_id=technology.id))

    if data.tag_names is not None:
        incident.tag_links.clear()
        await session.flush()
        for name in data.tag_names:
            tag = await _get_or_create(session, Tag, name)
            incident.tag_links.append(IncidentTag(tag_id=tag.id))

    await session.commit()
    await _try_enrich(session, incident)
    await _try_embed(session, incident)
    return await get_incident(session, incident_id)


async def archive_incident(session: AsyncSession, incident_id: int) -> Incident | None:
    """Section 10/43: never hard-delete knowledge — archive instead."""
    incident = await get_incident(session, incident_id)
    if incident is None:
        return None
    incident.status = IncidentStatus.obsolete
    await session.commit()
    return await get_incident(session, incident_id)
