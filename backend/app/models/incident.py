"""Incident (Section 10) and Attempt (Section 12) — the heart of the schema.

`raw_problem` / `raw_solution` are sacred: the user's original text, never overwritten
by AI enrichment (Section 11). Everything else on Incident is either user-editable
structure or AI-derived (tracked per-field in ExtractionProvenance).
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.db.base import Base
from app.models.enums import IncidentStatus, Severity
from app.models.mixins import TimestampMixin
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.attachment import Attachment
    from app.models.command import Command
    from app.models.embedding import ChunkEmbedding
    from app.models.environment import Environment
    from app.models.provenance import ExtractionProvenance
    from app.models.relationships import (
        IncidentProject,
        IncidentRelation,
        IncidentTag,
        IncidentTechnology,
    )
    from app.models.revision import Revision


class Incident(Base, TimestampMixin):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Sacred raw user input (Section 11) — never overwritten by AI ---
    raw_problem: Mapped[str] = mapped_column(Text)
    raw_solution: Mapped[str | None] = mapped_column(Text)

    # --- AI-derivable structure (Section 10, 20) ---
    title: Mapped[str | None] = mapped_column(String(300), index=True)
    normalized_problem: Mapped[str | None] = mapped_column(Text)
    symptoms: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    solution: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    why_solution_worked: Mapped[str | None] = mapped_column(Text)
    lesson_learned: Mapped[str | None] = mapped_column(Text)

    status: Mapped[IncidentStatus] = mapped_column(
        SAEnum(IncidentStatus, native_enum=False, validate_strings=True),
        default=IncidentStatus.unresolved,
        server_default=IncidentStatus.unresolved.value,
        index=True,
    )
    severity: Mapped[Severity | None] = mapped_column(
        SAEnum(Severity, native_enum=False, validate_strings=True)
    )
    confidence: Mapped[float | None]

    # Section 21: AI must not invent facts — flips true whenever the HeuristicProvider
    # (no LLM available) produced this incident's structure, so a human can revisit it.
    needs_ai_review: Mapped[bool] = mapped_column(default=False, server_default="0")

    solved_at: Mapped[datetime | None]
    last_verified_at: Mapped[datetime | None]

    # --- Relationships ---
    environment: Mapped["Environment | None"] = relationship(
        back_populates="incident", uselist=False, cascade="all, delete-orphan"
    )
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="Attempt.order"
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    commands: Mapped[list["Command"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    provenance: Mapped[list["ExtractionProvenance"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["ChunkEmbedding"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    technology_links: Mapped[list["IncidentTechnology"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    project_links: Mapped[list["IncidentProject"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    tag_links: Mapped[list["IncidentTag"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    outgoing_relations: Mapped[list["IncidentRelation"]] = relationship(
        foreign_keys="IncidentRelation.source_incident_id",
        back_populates="source_incident",
        cascade="all, delete-orphan",
    )
    incoming_relations: Mapped[list["IncidentRelation"]] = relationship(
        foreign_keys="IncidentRelation.target_incident_id",
        back_populates="target_incident",
        cascade="all, delete-orphan",
    )


class Attempt(Base):
    """A failed (or successful) attempt — first-class knowledge (Section 12)."""

    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    order: Mapped[int] = mapped_column(Integer, default=0)

    action: Mapped[str] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    why_failed: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="attempts")
