"""Pydantic schemas for the Incident API/MCP surface (Phase 3, Section 10/18).

Kept deliberately separate from the SQLAlchemy models (docs/RESEARCH.md § Backend
Stack rejected SQLModel for exactly this reason): the DB is canonical, these are a view.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IncidentStatus, Severity


class EnvironmentIn(BaseModel):
    operating_system: str | None = None
    os_version: str | None = None
    architecture: str | None = None
    hardware: str | None = None
    cpu: str | None = None
    gpu: str | None = None
    device: str | None = None
    firmware: str | None = None
    driver_versions: str | None = None
    language: str | None = None
    runtime: str | None = None
    compiler: str | None = None
    framework: str | None = None
    library: str | None = None
    library_versions: str | None = None
    container: str | None = None
    configuration: str | None = None


class EnvironmentRead(EnvironmentIn):
    model_config = ConfigDict(from_attributes=True)


class AttemptIn(BaseModel):
    action: str
    command: str | None = None
    hypothesis: str | None = None
    result: str | None = None
    why_failed: str | None = None


class AttemptRead(AttemptIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order: int
    created_at: datetime


# --- Zero-friction capture (Section 18): only raw_problem is required. ---
class IncidentCreate(BaseModel):
    raw_problem: str = Field(min_length=1)
    raw_solution: str | None = None


class QuickCaptureIn(BaseModel):
    """Section 19: paste one blob, save immediately, structure it later (async)."""

    raw_text: str = Field(min_length=1)


class IncidentUpdate(BaseModel):
    """Everything the zero-friction create endpoint deliberately omits (Section 18)
    can be filled in afterwards — by a human or by AI enrichment (Phase 10)."""

    raw_solution: str | None = None
    title: str | None = None
    normalized_problem: str | None = None
    symptoms: str | None = None
    root_cause: str | None = None
    solution: str | None = None
    explanation: str | None = None
    why_solution_worked: str | None = None
    lesson_learned: str | None = None
    status: IncidentStatus | None = None
    severity: Severity | None = None
    confidence: float | None = None
    needs_ai_review: bool | None = None
    environment: EnvironmentIn | None = None
    attempts: list[AttemptIn] | None = None
    project_names: list[str] | None = None
    technology_names: list[str] | None = None
    tag_names: list[str] | None = None


class IncidentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    status: IncidentStatus
    severity: Severity | None
    needs_ai_review: bool
    created_at: datetime
    updated_at: datetime


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_problem: str
    raw_solution: str | None
    title: str | None
    normalized_problem: str | None
    symptoms: str | None
    root_cause: str | None
    solution: str | None
    explanation: str | None
    why_solution_worked: str | None
    lesson_learned: str | None
    status: IncidentStatus
    severity: Severity | None
    confidence: float | None
    needs_ai_review: bool
    solved_at: datetime | None
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    environment: EnvironmentRead | None
    attempts: list[AttemptRead]


class PossibleDuplicate(BaseModel):
    incident_id: int
    title: str | None
    similarity_hint: str = "lexical"


class IncidentCreateResponse(BaseModel):
    incident: IncidentRead
    possible_duplicates: list[PossibleDuplicate] = []
