"""AI enrichment provenance (Section 21) — per-field basis/confidence/evidence.

Rebuildable/derived: re-running extraction against unchanged canonical evidence produces
new rows here without ever touching Incident.raw_problem/raw_solution.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ProvenanceBasis

if TYPE_CHECKING:
    from app.models.incident import Incident


class ExtractionProvenance(Base):
    __tablename__ = "extraction_provenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )

    field_name: Mapped[str] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text)
    basis: Mapped[ProvenanceBasis] = mapped_column(
        SAEnum(ProvenanceBasis, native_enum=False, validate_strings=True)
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_quote: Mapped[str | None] = mapped_column(Text)

    extractor_provider: Mapped[str] = mapped_column(String(50))
    extractor_model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    extracted_at: Mapped[datetime]

    incident: Mapped["Incident"] = relationship(back_populates="provenance")
