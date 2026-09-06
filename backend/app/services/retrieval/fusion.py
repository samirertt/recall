"""Weighted-linear score fusion (Phase 9; docs/RESEARCH.md § Hybrid Retrieval).

Pure functions over plain score dicts — no DB/model access — so fusion math is
unit-testable without spinning up SQLite or an embedding model (per the research
decision record's stated design goal).

Deviations from the full research design, flagged rather than silently omitted:
- No dedicated exact-identifier-extraction subsystem yet (`incident_identifiers`
  table + short-circuit) — the trigram substring signal is a partial stand-in.
  Deferred; see IMPLEMENTATION_CHECKLIST.md.
- Normalization uses rank-based reciprocal falloff for lexical, not full min-max
  over raw bm25 magnitudes — simpler, monotonic, and bounded; a legitimate variant,
  not the literal scheme described in research, and documented as such.
"""

WEIGHTS_PROFILE = "default-v1"

DEFAULT_WEIGHTS = {
    "lexical": 0.35,
    "trigram": 0.10,
    "vector": 0.35,
    "attachment": 0.10,
    "relationship": 0.10,
}

RELATIONSHIP_SCORE = 0.4  # fixed decay for a 1-hop relationship-only candidate (docs/RESEARCH.md)
MAX_RELATIONSHIP_SEEDS = 10  # bound traversal cost: only expand from the top seed candidates
MAX_RELATIONSHIP_ADDITIONS = 5  # fixed small slot count — recall aid, not a ranking signal


def active_weights(vector_available: bool) -> dict[str, float]:
    """Auto-renormalizes when the vector signal is unavailable, so its weight mass
    isn't silently dropped (docs/ARCHITECTURE.md § 5 step 4)."""
    weights = dict(DEFAULT_WEIGHTS)
    if vector_available:
        return weights
    vector_weight = weights.pop("vector")
    total = sum(weights.values())
    return {key: value + (value / total) * vector_weight for key, value in weights.items()}


def fuse(
    signal_scores: dict[str, float], weights: dict[str, float]
) -> float:
    """`signal_scores` maps signal name -> normalized score in [0, 1] for one
    incident; missing signals contribute 0, not an error."""
    return round(
        sum(weights.get(signal, 0.0) * score for signal, score in signal_scores.items()), 4
    )
