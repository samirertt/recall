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
    """Section 18: zero-friction capture. Must always succeed synchronously —
    no AI/embedding/attachment dependency sits on this path (docs/ARCHITECTURE.md § 3)."""
    incident = Incident(
        raw_problem=data.raw_problem,
        raw_solution=data.raw_solution,
        status=IncidentStatus.solved if data.raw_solution else IncidentStatus.unresolved,
        # No AI provider wired up yet (Phase 10) — flag for later review rather than
        # silently leaving title/root_cause/etc. unset with no signal (Section 21).
        needs_ai_review=True,
        title=_heuristic_title(data.raw_problem),
    )
    session.add(incident)
    await session.commit()
    await _try_embed(session, incident)
    return await get_incident(session, incident.id)


async def quick_capture(session: AsyncSession, data: QuickCaptureIn) -> Incident:
    """Section 19: save immediately; structuring happens later (Phase 10, async)."""
    return await create_incident(session, IncidentCreate(raw_problem=data.raw_text))


def _heuristic_title(raw_problem: str, max_len: int = 120) -> str:
    """Zero-LLM fallback title (docs/ARCHITECTURE.md § 6 HeuristicProvider): first
    non-blank line, truncated. Replaced by AI-generated title once Phase 10 lands."""
    for line in raw_problem.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_len]
    return raw_problem[:max_len]


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
