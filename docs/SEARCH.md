# Search

Reference for how retrieval actually works. Design rationale and alternatives
considered live in [docs/RESEARCH.md](RESEARCH.md); this is the "how it works today"
summary, kept in sync with the code in `backend/app/services/retrieval/` and
`backend/app/services/embeddings/`.

## Signals

| Signal | Backing | Always available? |
|---|---|---|
| Lexical | SQLite FTS5 (`incidents_fts`), BM25-ranked, column-weighted | Yes |
| Trigram (substring) | SQLite FTS5 (`incidents_fts_trigram`) | Yes |
| Attachment text | SQLite FTS5 (`extracted_texts_fts`) | Yes (depends on extraction having succeeded for that attachment) |
| Vector (semantic) | NumPy brute-force cosine over `chunk_embeddings` | Only if the embedding model loaded (`GET /health` reports this) |
| Relationship expansion | 1-hop `WITH RECURSIVE` traversal from top seed results | Yes |

### FTS5 tokenization

`incidents_fts` uses `unicode61` with `tokenchars '_.-:#/'` so identifiers like
`ECONNREFUSED`, `NullPointerException`, or `HTTP_503` stay single tokens instead of
being split on every punctuation character — the single tuning knob that matters most
for this corpus (see docs/RESEARCH.md § SQLite & FTS5).

### Query safety

User queries are never passed to FTS5 raw. `_build_match_query()` (`lexical.py`)
tokenizes the query with a simple regex, then **individually double-quotes every
token** before joining them. This means arbitrary input — including literal FTS5
operators like `AND`/`NOT`, column filters (`col:term`), parentheses, or `*` — can
never be parsed as a query operator, only as literal text to search for.

Tokens are joined with explicit `OR` (not FTS5's default implicit `AND` for a bare
token sequence) — a real bug this project shipped and then caught with its own
retrieval benchmark: implicit AND silently requires *every* query word to appear
verbatim in a matching row, which breaks any natural-language query longer than 2-3
words. `OR` + `bm25()` ranking is the standard IR model instead: BM25's own IDF
weighting already rewards rare/specific terms over common ones.

## Fusion

`app/services/retrieval/fusion.py` implements weighted-linear fusion:

```
fused_score = Σ weight[signal] × normalized_score[signal]
```

Default weights (`DEFAULT_WEIGHTS`): lexical 0.35, vector 0.35, trigram 0.10,
attachment 0.10, relationship 0.10. If the embedding provider is unavailable,
`active_weights(vector_available=False)` drops `vector` and **proportionally
redistributes its weight** across the remaining signals — the vector signal's weight
mass is never silently dropped, and `degraded.vector_search` in the API response
tells the caller this happened.

Normalization: lexical uses reciprocal-rank falloff (`1/(1+rank)`) rather than a
literal min-max of raw BM25 magnitudes — simpler, monotonic, bounded in (0,1], and a
documented, deliberate variant of the scheme in docs/RESEARCH.md (see
`_rank_normalize()` in `search.py`). Trigram and attachment hits are binary (1.0 if
found). Vector uses the raw cosine similarity, clamped to ≥0.

Relationship expansion pulls 1-hop neighbors of the top seed results (capped at 5
additions, from at most the top 10 seeds) with a fixed decayed score (0.4) — a recall
aid, not a primary ranking signal, matching docs/RESEARCH.md's "fixed small slot
count" design.

## What's not built yet

- **Exact-identifier short-circuit.** docs/RESEARCH.md describes a dedicated
  `incident_identifiers` table populated by a regex library at ingestion time, with
  exact matches pinned to the top of results regardless of fused score. Not built —
  the trigram substring signal is a partial stand-in (it catches exact identifiers,
  but doesn't short-circuit ranking).
- **Metadata/environment-aware filtering on the search endpoint.** The retrieval
  pipeline is environment-*aware* at the data-model level (Environment is a
  first-class entity per incident), but `GET /search` doesn't yet accept
  project/technology/environment filters as query parameters.
- **Reranking.** Deliberately deferred per docs/RESEARCH.md — not justified at this
  project's scale (comfortably under 100k rows). The pipeline has a natural slot for
  a pluggable post-fusion cross-encoder stage if this changes.
- **Configurable weights profile.** Weights are a Python constant
  (`fusion.DEFAULT_WEIGHTS`), not yet a user-editable `ranking.weights.json`/settings
  row as docs/RESEARCH.md describes — though every result already stamps
  `weights_profile` for forward-compatible explainability.

## Retrieval evaluation

`backend/tests/test_retrieval_eval.py` + `backend/tests/retrieval/fixtures/` is a
labeled benchmark (~32 synthetic incidents, one confusable pair per required
category — CUDA/ROS2/Docker/C++/Python/Linux/networking/Git/databases/embedded/
computer-vision/build-systems — plus filler incidents for discrimination), run in
both `hybrid` and `lexical_only` profiles and gated against `thresholds.yaml`.

This is evaluation-as-regression-test: the goal is "did this change make search
worse," not an absolute quality claim. Measured baseline: precision@1 0.958,
recall@10 1.0, MRR 0.979, pairwise-environment-accuracy 0.80 (both profiles — see
IMPLEMENTATION_CHECKLIST.md for why they're currently close). Growing the benchmark
is pure data work — append entries to `corpus.yaml`/`queries.yaml`, no harness
changes needed — and any real search miss noticed while using the tool is worth
adding as a new fixture pair.

## Performance

No formal load testing has been done yet (Phase 17 in
IMPLEMENTATION_CHECKLIST.md). At this project's target scale (comfortably under
10,000 incidents), lexical search is a single indexed FTS5 query and the vector scan
is a NumPy matmul over a matrix that fits in memory (roughly 15MB at 10k incidents ×
384 dimensions) — both are expected to be well within the <100ms/<500ms targets from
Section 58 of the source spec, but this has not been measured end-to-end under
realistic data volume.
