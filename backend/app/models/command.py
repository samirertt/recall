"""First-class, copyable command knowledge (Section 49)."""

from typing import TYPE_CHECKING

from app.db.base import Base
from app.models.mixins import TimestampMixin
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.incident import Incident


class Command(Base, TimestampMixin):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("attempts.id", ondelete="SET NULL"))

    command_text: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    incident: Mapped["Incident"] = relationship(back_populates="commands")
