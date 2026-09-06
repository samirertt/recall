"""Deterministic provenance verification (docs/RESEARCH.md § AI Provider Abstraction):
don't trust an LLM's self-reported confidence — anchor trust to the canonical evidence
text instead.

Simplification vs. the full research design: rather than asking the model to emit a
verbatim `evidence_quote` per field (which doubles schema complexity across two
provider wire formats), a narrative field's own text is fuzzy-matched against the raw
incident text directly. High overlap -> `explicit` (the model is closely quoting/
paraphrasing); low overlap -> `synthesized` (genuine summarization, expected and fine
for fields like root_cause/lesson_learned — not itself a red flag). This is a real,
deterministic verification step, just a simpler one than per-field LLM-emitted quotes.
"""

from rapidfuzz import fuzz

from app.models.enums import ProvenanceBasis
from app.schemas.extraction import FieldProvenance

EXPLICIT_THRESHOLD = 75.0  # rapidfuzz partial_ratio (0-100)
SYNTHESIZED_DEFAULT_CONFIDENCE = 0.6


def verify_field(field_name: str, value: str | None, raw_text: str) -> FieldProvenance:
    if not value:
        return FieldProvenance(value=None, basis=ProvenanceBasis.unknown, confidence=None)

    ratio = fuzz.partial_ratio(value, raw_text)
    if ratio >= EXPLICIT_THRESHOLD:
        return FieldProvenance(
            value=value, basis=ProvenanceBasis.explicit, confidence=round(ratio / 100.0, 3)
        )
    return FieldProvenance(
        value=value, basis=ProvenanceBasis.synthesized, confidence=SYNTHESIZED_DEFAULT_CONFIDENCE
    )


def verify_list_field(field_name: str, values: list[str], raw_text: str) -> list[FieldProvenance]:
    lowered = raw_text.lower()
    results = []
    for value in values:
        if value.lower() in lowered:
            results.append(
                FieldProvenance(value=value, basis=ProvenanceBasis.explicit, confidence=1.0)
            )
        else:
            results.append(
                FieldProvenance(value=value, basis=ProvenanceBasis.inferred, confidence=0.5)
            )
    return results
