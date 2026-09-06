"""Versioned knowledge (Section 43) — old solutions are superseded, never destroyed."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.incident import Incident


class Revision(Base, TimestampMixin):
    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )

    field_name: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)

    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id"))
    user_verified: Mapped[bool] = mapped_column(default=False, server_default="0")
    validated_at: Mapped[datetime | None]

    incident: Mapped["Incident"] = relationship(back_populates="revisions")
