"""Import every model module so Base.metadata / Alembic autogenerate see the full schema."""

from app.models.attachment import Attachment, ExtractedText
from app.models.command import Command
from app.models.embedding import ChunkEmbedding
from app.models.environment import Environment
from app.models.incident import Attempt, Incident
from app.models.provenance import ExtractionProvenance
from app.models.reference import Project, Tag, Technology
from app.models.relationships import (
    IncidentProject,
    IncidentRelation,
    IncidentTag,
    IncidentTechnology,
    TechnologyRelation,
)
from app.models.revision import Revision

__all__ = [
    "Attachment",
    "ExtractedText",
    "Command",
    "ChunkEmbedding",
    "Environment",
    "Attempt",
    "Incident",
    "ExtractionProvenance",
    "Project",
    "Tag",
    "Technology",
    "IncidentProject",
    "IncidentRelation",
    "IncidentTag",
    "IncidentTechnology",
    "TechnologyRelation",
    "Revision",
]
