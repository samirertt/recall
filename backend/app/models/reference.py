"""Reusable reference entities: Project, Technology, Tag (Section 14/15 of the spec)."""

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.relationships import TechnologyRelation


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(2000))


class Technology(Base, TimestampMixin):
    __tablename__ = "technologies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(2000))

    outgoing_relations: Mapped[list["TechnologyRelation"]] = relationship(
        foreign_keys="TechnologyRelation.source_technology_id",
        back_populates="source_technology",
        cascade="all, delete-orphan",
    )
    incoming_relations: Mapped[list["TechnologyRelation"]] = relationship(
        foreign_keys="TechnologyRelation.target_technology_id",
        back_populates="target_technology",
        cascade="all, delete-orphan",
    )


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
