from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ExtractionStatus


class ExtractedTextRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: ExtractionStatus
    text: str | None
    confidence: float | None
    extractor_engine: str | None
    error: str | None
    extracted_at: datetime


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    filename: str
    mime_type: str | None
    size_bytes: int
    sha256: str
    created_at: datetime
