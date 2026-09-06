"""Model/relationship tests (Phase 2)."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (
    Attempt,
    Environment,
    Incident,
    IncidentRelation,
    IncidentTechnology,
    Technology,
)
from app.models.enums import AttributionSource, IncidentRelationType, IncidentStatus


async def test_incident_create_with_attempts_and_environment(db_session):
    incident = Incident(
        raw_problem="Jetson cannot detect CUDA from PyTorch.",
        raw_solution="Installed the CUDA-enabled PyTorch build.",
        status=IncidentStatus.solved,
    )
    incident.attempts.append(
        Attempt(order=0, action="Reinstall PyTorch", result="CUDA still unavailable")
    )
    incident.environment = Environment(
        operating_system="Ubuntu", os_version="22.04", gpu="Jetson Orin NX"
    )
    db_session.add(incident)
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(Incident).where(Incident.id == incident.id)
        )
    ).scalar_one()
    assert fetched.raw_problem.startswith("Jetson")
    assert fetched.raw_solution == "Installed the CUDA-enabled PyTorch build."  # sacred, unedited
    await db_session.refresh(fetched, attribute_names=["attempts", "environment"])
    assert len(fetched.attempts) == 1
    assert fetched.attempts[0].action == "Reinstall PyTorch"
    assert fetched.environment.gpu == "Jetson Orin NX"


async def test_incident_technology_link_with_provenance(db_session):
    incident = Incident(raw_problem="p", raw_solution="s")
    cuda = Technology(name="CUDA")
    db_session.add_all([incident, cuda])
    await db_session.flush()

    db_session.add(
        IncidentTechnology(
            incident_id=incident.id,
            technology_id=cuda.id,
            relevance="root_cause",
            source=AttributionSource.ai_extracted,
            confidence=0.9,
        )
    )
    await db_session.commit()

    links = (
        await db_session.execute(
            select(IncidentTechnology).where(IncidentTechnology.incident_id == incident.id)
        )
    ).scalars().all()
    assert len(links) == 1
    assert links[0].source == AttributionSource.ai_extracted


async def test_incident_relation_recursive_traversal(db_session):
    a, b, c = (Incident(raw_problem=f"p{i}") for i in range(3))
    db_session.add_all([a, b, c])
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            IncidentRelation(
                source_incident_id=a.id,
                target_incident_id=b.id,
                relation_type=IncidentRelationType.related_to,
                created_at=now,
            ),
            IncidentRelation(
                source_incident_id=b.id,
                target_incident_id=c.id,
                relation_type=IncidentRelationType.related_to,
                created_at=now,
            ),
        ]
    )
    await db_session.commit()

    from app.services.knowledge_graph.traversal import related_incident_ids

    two_hop = await related_incident_ids(db_session, start_id=a.id, max_depth=2)
    assert set(two_hop) == {b.id, c.id}

    one_hop = await related_incident_ids(db_session, start_id=a.id, max_depth=1)
    assert set(one_hop) == {b.id}


async def test_incident_relation_rejects_self_reference(db_session):
    incident = Incident(raw_problem="p")
    db_session.add(incident)
    await db_session.flush()

    db_session.add(
        IncidentRelation(
            source_incident_id=incident.id,
            target_incident_id=incident.id,
            relation_type=IncidentRelationType.related_to,
        )
    )
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await db_session.commit()
