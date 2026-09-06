from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AttributionSource, IncidentRelationType


class RelationCreate(BaseModel):
    target_incident_id: int
    relation_type: IncidentRelationType
    note: str | None = None


class RelationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_incident_id: int
    target_incident_id: int
    relation_type: IncidentRelationType
    source: AttributionSource
    confidence: float | None
    note: str | None
    created_at: datetime


class RelatedIncident(BaseModel):
    incident_id: int
    title: str | None
    depth: int


class SuggestedRelation(BaseModel):
    incident_id: int
    title: str | None
    similarity: float
    suggested_relation_type: IncidentRelationType = IncidentRelationType.related_to
