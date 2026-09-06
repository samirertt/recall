"""Structured environment capture (Section 13 of the spec) — influences retrieval."""

from typing import TYPE_CHECKING

from app.db.base import Base
from app.models.mixins import TimestampMixin
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.incident import Incident


class Environment(Base, TimestampMixin):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True, index=True
    )

    operating_system: Mapped[str | None] = mapped_column(String(200))
    os_version: Mapped[str | None] = mapped_column(String(100))
    architecture: Mapped[str | None] = mapped_column(String(50))
    hardware: Mapped[str | None] = mapped_column(String(200))
    cpu: Mapped[str | None] = mapped_column(String(200))
    gpu: Mapped[str | None] = mapped_column(String(200))
    device: Mapped[str | None] = mapped_column(String(200))
    firmware: Mapped[str | None] = mapped_column(String(200))
    driver_versions: Mapped[str | None] = mapped_column(String(1000))  # freeform "cuda=12.4,..."
    language: Mapped[str | None] = mapped_column(String(100))
    runtime: Mapped[str | None] = mapped_column(String(200))
    compiler: Mapped[str | None] = mapped_column(String(200))
    framework: Mapped[str | None] = mapped_column(String(200))
    library: Mapped[str | None] = mapped_column(String(200))
    library_versions: Mapped[str | None] = mapped_column(String(1000))
    container: Mapped[str | None] = mapped_column(String(200))
    configuration: Mapped[str | None] = mapped_column(String(2000))

    incident: Mapped["Incident"] = relationship(back_populates="environment")
