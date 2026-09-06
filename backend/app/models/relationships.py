"""The knowledge graph (Section 28/29, docs/ARCHITECTURE.md § 4): plain relational
SQLite tables, not a dedicated graph database (see docs/RESEARCH.md § Knowledge Graph).

Bipartite association tables carry extra columns (relevance/source/confidence), so they
are mapped classes rather than a bare `Table` — this is the SQLAlchemy 2.0 "association
object" pattern.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.db.base import Base
from app.models.enums import AttributionSource, IncidentRelationType, TechnologyRelationType
from sqlalchemy import CheckConstraint, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.reference import Project, Tag, Technology


class IncidentTechnology(Base):
    __tablename__ = "incident_technologies"

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    technology_id: Mapped[int] = mapped_column(
        ForeignKey("technologies.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    relevance: Mapped[str | None] = mapped_column(String(50))  # 'root_cause'|'affected'|'mentioned'
    source: Mapped[AttributionSource] = mapped_column(
        SAEnum(AttributionSource, native_enum=False, validate_strings=True),
        default=AttributionSource.human,
        server_default=AttributionSource.human.value,
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="technology_links")
    technology: Mapped["Technology"] = relationship()


class IncidentProject(Base):
    __tablename__ = "incident_projects"

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="project_links")
    project: Mapped["Project"] = relationship()


class IncidentTag(Base):
    __tablename__ = "incident_tags"

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="tag_links")
    tag: Mapped["Tag"] = relationship()


class IncidentRelation(Base):
    """Typed, self-referencing Incident<->Incident edges (Section 28/29).

    Directionality convention (see docs/ARCHITECTURE.md § 4): for `caused_by`,
    `solved_by`, `supersedes` — `source A <relation_type> target B` reads exactly like
    the English sentence, e.g. "A caused_by B" means B is the cause of A. `related_to`
    and `duplicate_of` are symmetric: always insert with source_incident_id <
    target_incident_id (enforced below) and match both directions at query time.
    """

    __tablename__ = "incident_relations"
    __table_args__ = (
        UniqueConstraint("source_incident_id", "target_incident_id", "relation_type"),
        CheckConstraint("source_incident_id != target_incident_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    target_incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[IncidentRelationType] = mapped_column(
        SAEnum(IncidentRelationType, native_enum=False, validate_strings=True)
    )
    source: Mapped[AttributionSource] = mapped_column(
        SAEnum(AttributionSource, native_enum=False, validate_strings=True),
        default=AttributionSource.human,
        server_default=AttributionSource.human.value,
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    source_incident: Mapped["Incident"] = relationship(
        foreign_keys=[source_incident_id], back_populates="outgoing_relations"
    )
    target_incident: Mapped["Incident"] = relationship(
        foreign_keys=[target_incident_id], back_populates="incoming_relations"
    )


class TechnologyRelation(Base):
    """Typed, self-referencing Technology<->Technology edges (Section 28)."""

    __tablename__ = "technology_relations"
    __table_args__ = (
        UniqueConstraint("source_technology_id", "target_technology_id", "relation_type"),
        CheckConstraint("source_technology_id != target_technology_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_technology_id: Mapped[int] = mapped_column(
        ForeignKey("technologies.id", ondelete="CASCADE"), index=True
    )
    target_technology_id: Mapped[int] = mapped_column(
        ForeignKey("technologies.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[TechnologyRelationType] = mapped_column(
        SAEnum(TechnologyRelationType, native_enum=False, validate_strings=True)
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    source_technology: Mapped["Technology"] = relationship(
        foreign_keys=[source_technology_id], back_populates="outgoing_relations"
    )
    target_technology: Mapped["Technology"] = relationship(
        foreign_keys=[target_technology_id], back_populates="incoming_relations"
    )
