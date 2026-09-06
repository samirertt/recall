"""Structured extraction schema (Phase 10; docs/RESEARCH.md § AI Provider Abstraction).

`IncidentExtraction` is the single source of truth every provider adapter targets —
one schema, thin per-vendor adapters, never three parallel hand-maintained schemas.
"""

from pydantic import BaseModel, Field

from app.models.enums import ProvenanceBasis


class IncidentExtraction(BaseModel):
    """What the AI is asked to produce. Never touches raw_problem/raw_solution —
    those are sacred (Section 11) and this schema has no field for them."""

    title: str = Field(description="A short, specific title for this incident.")
    normalized_problem: str | None = Field(
        default=None, description="The problem restated clearly and concisely."
    )
    symptoms: str | None = Field(default=None, description="Observable symptoms, if stated.")
    root_cause: str | None = Field(
        default=None, description="The underlying cause, if determinable from the text."
    )
    solution: str | None = Field(default=None, description="What resolved it, if stated.")
    why_solution_worked: str | None = Field(
        default=None, description="Why the solution worked, if the text explains it."
    )
    lesson_learned: str | None = Field(
        default=None, description="A reusable lesson, if one can be drawn."
    )
    tags: list[str] = Field(default_factory=list, description="Short lowercase keyword tags.")
    technologies: list[str] = Field(
        default_factory=list, description="Named technologies/tools/frameworks involved."
    )


class FieldProvenance(BaseModel):
    value: str | None
    basis: ProvenanceBasis
    confidence: float | None = None
    evidence_quote: str | None = None


class ExtractionResult(BaseModel):
    fields: IncidentExtraction
    provenance: dict[str, FieldProvenance]
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    raw_response: str | None = None
