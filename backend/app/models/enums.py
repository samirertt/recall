import enum


class IncidentStatus(enum.StrEnum):
    unresolved = "unresolved"
    investigating = "investigating"
    solved = "solved"
    abandoned = "abandoned"
    obsolete = "obsolete"


class Severity(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ProvenanceBasis(enum.StrEnum):
    explicit = "explicit"
    inferred = "inferred"
    synthesized = "synthesized"
    unknown = "unknown"


class AttributionSource(enum.StrEnum):
    """Who/what asserted a fact — a tag/technology link, a relationship, etc."""

    human = "human"
    ai_extracted = "ai_extracted"
    ai_suggested = "ai_suggested"


class IncidentRelationType(enum.StrEnum):
    related_to = "related_to"
    caused_by = "caused_by"
    solved_by = "solved_by"
    supersedes = "supersedes"
    duplicate_of = "duplicate_of"


# is_symmetric(relation_type): related_to/duplicate_of read the same in both directions;
# caused_by/solved_by/supersedes are directional (see docs/ARCHITECTURE.md § 4).
SYMMETRIC_INCIDENT_RELATION_TYPES = {
    IncidentRelationType.related_to,
    IncidentRelationType.duplicate_of,
}


class TechnologyRelationType(enum.StrEnum):
    depends_on = "depends_on"
    part_of = "part_of"
    replaces = "replaces"
    related_to = "related_to"


class ExtractionStatus(enum.StrEnum):
    success = "success"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"
