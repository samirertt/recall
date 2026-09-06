"""AI enrichment orchestration (Phase 10; docs/ARCHITECTURE.md § 6).

Populates derived fields from raw_problem/raw_solution — never touches those sacred
fields themselves (Section 11), and never overwrites a field a human or an earlier
enrichment pass already set (Section 21: AI-generated content must never silently
replace something already there). Always best-effort: a failure here must never
block incident capture/edit (docs/ARCHITECTURE.md § 10).
"""

from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.models.provenance import ExtractionProvenance
from app.services.ai.provider import AIProvider, get_ai_provider

_DERIVED_FIELDS = (
    "title",
    "normalized_problem",
    "symptoms",
    "root_cause",
    "solution",
    "why_solution_worked",
    "lesson_learned",
)


def _incident_text(incident: Incident) -> str:
    # raw_problem stays unprefixed and first: HeuristicProvider's title comes from the
    # first non-blank line of whatever text it's given, so prefixing it here would
    # leak into every heuristic-generated title (e.g. "Problem: Jetson cannot ...").
    parts = [incident.raw_problem]
    if incident.raw_solution:
        parts.append(f"Solution: {incident.raw_solution}")
    return "\n\n".join(parts)


async def enrich_incident(
    session: AsyncSession, incident: Incident, provider: AIProvider | None = None
) -> bool:
    """Returns True if enrichment ran (regardless of provider tier). Never raises."""
    provider = provider or get_ai_provider()

    result = await provider.extract(_incident_text(incident))

    for field_name in _DERIVED_FIELDS:
        current = getattr(incident, field_name)
        new_value = getattr(result.fields, field_name)
        if current is None and new_value:
            setattr(incident, field_name, new_value)

    await session.execute(
        delete(ExtractionProvenance).where(ExtractionProvenance.incident_id == incident.id)
    )
    now = datetime.now(UTC)
    for field_name, field_provenance in result.provenance.items():
        session.add(
            ExtractionProvenance(
                incident_id=incident.id,
                field_name=field_name,
                value=field_provenance.value,
                basis=field_provenance.basis,
                confidence=field_provenance.confidence,
                evidence_quote=field_provenance.evidence_quote,
                extractor_provider=result.provider,
                extractor_model=result.model,
                prompt_version=result.prompt_version,
                schema_version=result.schema_version,
                extracted_at=now,
            )
        )

    # A real AI (not just the heuristic fallback) has now looked at this incident.
    if provider.name != "heuristic":
        incident.needs_ai_review = False

    await session.commit()
    return True
