"""Attachment metadata (Section 16) and extracted text (Section 17).

Original bytes live under data/attachments/<incident-id>/ — this table stores only
metadata + a path reference. `ExtractedText` is a rebuildable cache keyed by the
attachment's content hash, never the source of truth.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.db.base import Base
from app.models.enums import ExtractionStatus
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.incident import Incident
from app.models.mixins import TimestampMixin


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )

    filename: Mapped[str] = mapped_column(String(500))
    relative_path: Mapped[str] = mapped_column(String(1000))  # under settings.attachments_dir
    mime_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    incident: Mapped["Incident"] = relationship(back_populates="attachments")
    extracted_texts: Mapped[list["ExtractedText"]] = relationship(
        back_populates="attachment", cascade="all, delete-orphan"
    )


class ExtractedText(Base):
    """Rebuildable: safe to wipe and reprocess from the original attachment bytes."""

    __tablename__ = "extracted_texts"

    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[ExtractionStatus] = mapped_column(
        SAEnum(ExtractionStatus, native_enum=False, validate_strings=True)
    )
    text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    extractor_engine: Mapped[str | None] = mapped_column(String(100))
    extractor_version: Mapped[str | None] = mapped_column(String(50))
    source_attachment_hash: Mapped[str] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime]

    attachment: Mapped["Attachment"] = relationship(back_populates="extracted_texts")
