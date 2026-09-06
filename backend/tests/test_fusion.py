"""Unit tests for pure fusion math (Phase 9) — no DB/model access needed."""

from app.services.retrieval.fusion import DEFAULT_WEIGHTS, active_weights, fuse


def test_active_weights_unchanged_when_vector_available():
    weights = active_weights(vector_available=True)
    assert weights == DEFAULT_WEIGHTS
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_active_weights_renormalizes_when_vector_unavailable():
    weights = active_weights(vector_available=False)
    assert "vector" not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    # relative proportions among remaining signals are preserved
    assert weights["lexical"] > weights["trigram"]


def test_fuse_missing_signal_contributes_zero():
    weights = active_weights(vector_available=True)
    score = fuse({"lexical": 1.0}, weights)
    assert score == weights["lexical"]


def test_fuse_empty_scores_is_zero():
    assert fuse({}, active_weights(vector_available=True)) == 0.0


def test_fuse_all_signals_sums_correctly():
    weights = active_weights(vector_available=True)
    score = fuse({"lexical": 1.0, "trigram": 1.0, "vector": 1.0, "attachment": 1.0,
                  "relationship": 1.0}, weights)
    assert abs(score - 1.0) < 1e-9  # weights sum to 1, all scores are 1
