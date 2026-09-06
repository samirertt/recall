"""Relationship authoring (Phase 11; Section 28/29). Human-authored relations are
created directly; AI can only *suggest* (Section 29: "Do not automatically create
destructive relationships") — suggestions are a read-only computation over the
existing vector index, never written until a human (or the API caller) accepts one
via `create_relation`.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    SYMMETRIC_INCIDENT_RELATION_TYPES,
    AttributionSource,
    IncidentRelationType,
)
from app.models.incident import Incident
from app.models.relationships import IncidentRelation
from app.services.embeddings.service import search_vector
from app.services.knowledge_graph.traversal import related_incidents


async def create_relation(
    session: AsyncSession,
    source_incident_id: int,
    target_incident_id: int,
    relation_type: IncidentRelationType,
    source: AttributionSource = AttributionSource.human,
    confidence: float | None = None,
    note: str | None = None,
) -> IncidentRelation:
    """Enforces the canonical `source_id < target_id` ordering for symmetric relation
    types (docs/ARCHITECTURE.md § 4) so `related_to`/`duplicate_of` never end up
    stored as two conflicting directed rows for the same pair."""
    if (
        relation_type in SYMMETRIC_INCIDENT_RELATION_TYPES
        and source_incident_id > target_incident_id
    ):
        source_incident_id, target_incident_id = target_incident_id, source_incident_id

    relation = IncidentRelation(
        source_incident_id=source_incident_id,
        target_incident_id=target_incident_id,
        relation_type=relation_type,
        source=source,
        confidence=confidence,
        note=note,
    )
    session.add(relation)
    await session.commit()
    await session.refresh(relation)
    return relation


async def list_relations(session: AsyncSession, incident_id: int) -> list[IncidentRelation]:
    result = await session.execute(
        select(IncidentRelation).where(
            or_(
                IncidentRelation.source_incident_id == incident_id,
                IncidentRelation.target_incident_id == incident_id,
            )
        )
    )
    return list(result.scalars().all())


async def delete_relation(session: AsyncSession, relation_id: int) -> bool:
    relation = await session.get(IncidentRelation, relation_id)
    if relation is None:
        return False
    await session.delete(relation)
    await session.commit()
    return True


async def get_related_incidents(
    session: AsyncSession,
    incident_id: int,
    max_depth: int = 2,
    relation_type: IncidentRelationType | None = None,
) -> list[dict]:
    pairs = await related_incidents(
        session, incident_id, max_depth, relation_type.value if relation_type else None
    )
    if not pairs:
        return []
    ids = [incident_id for incident_id, _depth in pairs]
    incidents = {
        incident.id: incident
        for incident in (
            await session.execute(select(Incident).where(Incident.id.in_(ids)))
        )
        .scalars()
        .all()
    }
    return [
        {"incident_id": iid, "title": incidents[iid].title, "depth": depth}
        for iid, depth in pairs
        if iid in incidents
    ]


async def suggest_relations(
    session: AsyncSession, incident_id: int, limit: int = 5, min_similarity: float = 0.6
) -> list[dict]:
    """Section 29: AI-suggested relationships — computed on demand, never persisted
    until accepted. Requires the embedding provider; returns [] (not an error) if it's
    unavailable, matching the rest of the system's degradation behavior."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        return []

    already_related = {
        r.target_incident_id if r.source_incident_id == incident_id else r.source_incident_id
        for r in await list_relations(session, incident_id)
    }

    query_text = incident.title or incident.raw_problem
    hits = await search_vector(session, query_text, limit=limit + len(already_related) + 1)

    suggestions = []
    for hit in hits:
        candidate_id = hit["incident_id"]
        if candidate_id == incident_id or candidate_id in already_related:
            continue
        if hit["cosine_score"] < min_similarity:
            continue
        candidate = await session.get(Incident, candidate_id)
        if candidate is None:
            continue
        suggestions.append(
            {
                "incident_id": candidate_id,
                "title": candidate.title,
                "similarity": round(hit["cosine_score"], 4),
                "suggested_relation_type": IncidentRelationType.related_to,
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions
