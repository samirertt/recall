# Research — Engineering Memory

Research conducted 2026-09-06 for the initial architecture decisions of Engineering
Memory (personal engineering incident knowledge system). Each section below covers:
recommendation, alternatives considered, tradeoffs, portability/offline behavior,
complexity/maintenance, and migration risk — researched against current (Sept 2026)
documentation and package versions, not assumed defaults.

This is a **decision record**, not a tutorial. See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
for how these decisions compose into the actual system design.

## Key decisions at a glance

| Area | Decision |
|---|---|
| Canonical store | SQLite (single file) + evidence files on disk; everything else derived/rebuildable |
| Lexical search | SQLite FTS5 — external-content tables (prose: `unicode61` w/ tuned `tokenchars`; identifiers/logs: `trigram`), trigger-synced, BM25 ranking |
| Embeddings | `BAAI/bge-small-en-v1.5` (384-dim, MIT) via `fastembed` (ONNX runtime, no PyTorch) |
| Vector index | Plain NumPy brute-force cosine table (v1); upgrade path to `sqlite-vec` if it stops being fast enough (not expected at this scale) |
| Hybrid retrieval | Weighted linear fusion (exact-match + lexical + vector + relationship), RRF offered as alternate mode, exact-identifier short-circuit, no reranker in v1 |
| Retrieval evaluation | Small hand-labeled synthetic benchmark (~150-250 incidents, confusable pairs), P@K/Recall@K/MRR + pairwise environment-accuracy, run as a pytest gate |
| AI provider abstraction | Protocol-based `AIProvider` (Claude / OpenAI-compatible / local / heuristic-fallback), per-field provenance + basis + fuzzy-matched evidence quotes |
| Attachment ingestion | `charset-normalizer` (text), `PyMuPDF` + `pypdf` fallback (PDF), `Tesseract 5` via `pytesseract` (OCR) — all pluggable, none block save on failure |
| Knowledge graph | Plain relational SQLite tables (typed self-referencing edge tables), `WITH RECURSIVE` CTEs for N-hop traversal — no dedicated graph DB |
| Backup/portability | Zip: `manifest.json` + `core.db` (via `VACUUM INTO`) + content-addressed `attachments/`; derived indexes excluded by default (rebuild is cheaper than restore) |
| MCP integration | Official `mcp` Python SDK v2, `stdio` transport, `user`-scope registration, ~8-12 consolidated tools sharing the FastAPI service layer's Pydantic schemas |
| Claude Code integration | `claude mcp add --scope user`, short CLAUDE.md steering hint, tool descriptions (not the unreliable `instructions` field) as the steering channel |
| Backend | Python 3.13, FastAPI 0.141, Pydantic 2.13, SQLAlchemy 2.0.52 (async, `<2.1`), Alembic 1.19, `uv` for packaging |
| Frontend | React 19.2, Vite 8, TypeScript 6.0, Tailwind CSS 4.3, TanStack Query/Router/Table/Virtual, shadcn/ui, cmdk, react-force-graph |

---

## Table of Contents

1. [SQLite & FTS5](#sqlite--fts5)
2. [Local Embeddings](#local-embeddings)
3. [Vector Index](#vector-index)
4. [Hybrid Retrieval & Reranking](#hybrid-retrieval--reranking)
5. [Retrieval Evaluation](#retrieval-evaluation)
6. [AI Provider Abstraction & Structured Extraction](#ai-provider-abstraction--structured-extraction)
7. [Attachment Ingestion & OCR](#attachment-ingestion--ocr)
8. [Knowledge Graph](#knowledge-graph)
9. [MCP Integration](#mcp-integration)
10. [Claude Code Integration](#claude-code-integration)
11. [Backup, Portability & Migrations](#backup-portability--migrations)
12. [Backend Stack](#backend-stack)
13. [Frontend Stack](#frontend-stack)

---

## SQLite & FTS5

Engineering Memory's canonical store is a single SQLite file plus the original evidence files on disk; everything else — embeddings, vector index, and the full-text index — is a derived artifact that must be fully reconstructible from that canonical pair. Full-text search is also the one search modality that is required to work with zero dependencies (no AI, no embeddings, no external services), so its design has to be conservative, embedded, and self-healing. The corpus itself is a mix of free-text narrative (titles, summaries, root-cause writeups) and highly structured technical fragments (error codes, exception names, stack-trace frames, log lines, code snippets, file paths) that behave very differently under tokenization. FTS5 — the full-text search virtual table module built into SQLite — is the right engine for this; the decisions below are about *how* to configure it.

Current stable SQLite as of this writing is **3.53.4** (2026-07-24). The relevant FTS5 features assumed here — `contentless_delete=1` (added 3.43.0, 2023-08-24), `contentless_unindexed=1` and the v2 tokenizer API with locale support (added 3.47.0, 2024-10-21) — have been stable for well over a year and are safely within the recommended baseline below.

### Recommendation

Use **two purpose-built FTS5 virtual tables per document type, both external-content, synced by SQL triggers**, queried through `bm25()` with per-column weights, never treated as authoritative storage.

**1. Prose/narrative table — `unicode61` tokenizer, tuned `tokenchars`, no stemming.**

```sql
CREATE VIRTUAL TABLE incidents_fts USING fts5(
    title,
    summary,
    root_cause,
    resolution,
    tags,
    content='incidents',      -- external content: canonical table is source of truth
    content_rowid='id',
    tokenize = "unicode61 remove_diacritics 2 tokenchars '_.-:#/'"
);
```

- `content='incidents'` makes this an **external-content** table: FTS5 stores only the inverted index (postings), and reads column values from the real `incidents` row via `content_rowid` when it needs to answer `snippet()`/`highlight()`/`bm25()` or return matched text. No duplicated prose is stored twice on disk (beyond index overhead).
- `tokenchars '_.-:#/'` keeps identifier-shaped strings intact as single tokens — `NullPointerException`, `ECONNREFUSED`, `HTTP_503`, `com.foo.Bar`, `k8s-prod-node-03`, `#4271` — instead of `unicode61`'s default behavior of splitting on every non-alphanumeric character. This single tuning knob matters more for this corpus than any other tokenizer choice.
- **No `porter` stemming** on this table by default — see Alternatives Considered.

**2. Substring/identifier table — `trigram` tokenizer, for grep-style lookups inside logs and code.**

```sql
CREATE VIRTUAL TABLE incidents_fts_trigram USING fts5(
    raw_log_excerpt,
    code_snippet,
    content='incidents',
    content_rowid='id',
    tokenize = 'trigram case_sensitive 0'
);
```

Trigram indexes every 3-character window, so it answers "does this exact fragment appear anywhere" (a hex address, a partial UUID, a mangled log line, a half-remembered function name) regardless of word boundaries — something a token-based index structurally cannot do well. This table backs an explicit "search inside logs/code" mode, distinct from the ranked relevance search.

**Ranking with column weighting:**

```sql
SELECT id, title,
       bm25(incidents_fts, 10.0, 5.0, 3.0, 3.0, 1.0) AS rank
FROM incidents_fts
WHERE incidents_fts MATCH :query
ORDER BY rank;  -- bm25() returns lower = better; this ascending order is correct
```

Weights (title, summary, root_cause, resolution, tags) push exact title/summary matches above matches buried in a long resolution writeup. Trigram results are surfaced as a separate "found in raw logs/code" section rather than blended into the same ranked list — trigram matches on 3-char shingles have no meaningful BM25 relevance semantics next to token-based prose matches.

**Sync via triggers, not app-level code**, because correctness must not depend on every write path in the app remembering to update the index:

```sql
CREATE TRIGGER incidents_ai AFTER INSERT ON incidents BEGIN
  INSERT INTO incidents_fts(rowid, title, summary, root_cause, resolution, tags)
  VALUES (new.id, new.title, new.summary, new.root_cause, new.resolution, new.tags);
  INSERT INTO incidents_fts_trigram(rowid, raw_log_excerpt, code_snippet)
  VALUES (new.id, new.raw_log_excerpt, new.code_snippet);
END;

CREATE TRIGGER incidents_ad AFTER DELETE ON incidents BEGIN
  INSERT INTO incidents_fts(incidents_fts, rowid, title, summary, root_cause, resolution, tags)
  VALUES('delete', old.id, old.title, old.summary, old.root_cause, old.resolution, old.tags);
  INSERT INTO incidents_fts_trigram(incidents_fts_trigram, rowid, raw_log_excerpt, code_snippet)
  VALUES('delete', old.id, old.raw_log_excerpt, old.code_snippet);
END;

CREATE TRIGGER incidents_au AFTER UPDATE ON incidents BEGIN
  INSERT INTO incidents_fts(incidents_fts, rowid, title, summary, root_cause, resolution, tags)
  VALUES('delete', old.id, old.title, old.summary, old.root_cause, old.resolution, old.tags);
  INSERT INTO incidents_fts(rowid, title, summary, root_cause, resolution, tags)
  VALUES (new.id, new.title, new.summary, new.root_cause, new.resolution, new.tags);
  -- mirror for incidents_fts_trigram
END;
```

The `'delete'` command's supplied old-column values must exactly match what was indexed, which is guaranteed here since they come straight from `OLD.*` in the same statement. Complement the triggers with one Python-level maintenance function, `rebuild_fts()`, that runs `INSERT INTO incidents_fts(incidents_fts) VALUES('rebuild')` (and the trigram equivalent) — this is the "everything else is rebuildable" escape hatch: expose it as a CLI command and an MCP tool so both a human and Claude Code can self-heal a corrupted or drifted index without touching canonical data.

### Alternatives Considered

- **Contentless tables (`content=''`) as the primary/only FTS table.** Rejected. A fully contentless table returns `NULL` for every column except rowid, which breaks `snippet()`/`highlight()` — both need the original text to render match context. Since a canonical `incidents` table already exists as the source of truth, there's no storage argument for contentless either; external-content gives the same disk-space benefit (no duplicated text) while keeping snippet/highlight fully functional.
- **Contentless-delete tables (`contentless_delete=1`, `contentless_unindexed=1`).** A reasonable middle ground when there is *no* separate canonical table, but here it's strictly worse than external-content: it exists to let a contentless table behave like a normal writable table when you don't already have a source-of-truth row to point at. We already have one.
- **`porter` stemming as the default tokenizer.** Rejected as a blanket default. Porter helps recall for prose ("connect"/"connecting"/"connected") but is English-only and mangles exactly the tokens this corpus cares most about — error codes, class/exception names, and other non-English-shaped identifiers get stemmed into garbage or get merged with unrelated tokens. FTS5's tokenizer is set per virtual table, not per column, so "prose gets stemming, identifiers don't" isn't expressible in one table. Noted as a future opt-in (e.g., a third, stemmed shadow table over narrative columns only) if recall on natural-language incident descriptions proves insufficient in practice.
- **`ascii` tokenizer instead of `unicode61`.** Rejected as default: it doesn't casefold or normalize non-ASCII text at all, and engineering text realistically contains non-ASCII (accented names, unicode punctuation pasted from tickets/Slack, non-English comments). `unicode61` with `remove_diacritics 2` and custom `tokenchars` gets the identifier-preserving behavior we want without giving up Unicode-aware casefolding.
- **App-level (SQLAlchemy ORM-event) sync instead of DB triggers.** Rejected as the primary mechanism, kept as a secondary aid. Triggers are enforced at the database level regardless of code path — raw SQL, a bulk import script, a future admin tool, or a bug in one ORM code path all still keep the index in sync. App-level hooks only fire when that exact code path is exercised, which is a correctness footgun for something billed as "always works."
- **An external search engine (Elasticsearch, Meilisearch, Typesense, Postgres tsvector).** Rejected outright given the local-first constraint: any of these either requires a running service (violates "must degrade gracefully / always work offline") or a different database entirely (violates "SQLite is canonical"). FTS5 ships inside the SQLite binary already required for the rest of the app — zero additional operational surface.
- **Plain `LIKE '%term%'` / manual grep over rows instead of any FTS index.** Rejected as the primary mechanism (no ranking, full table scan, no index at all — becomes noticeably slow well before 10,000 rows with multi-column OR'd `LIKE` clauses), but this is effectively what the trigram table gives you *with* an index, so it's subsumed rather than needed separately.

### Tradeoffs

- **Two FTS tables instead of one** roughly doubles index-maintenance work per write (two trigger-driven inserts instead of one) and doubles the on-disk index footprint. At this project's scale (10,000+ incidents, each record realistically low-single-digit KB of text) this is immaterial in absolute terms — tens of MB, not GB — but it is real added write amplification and worth knowing if incident records ever grow to include e.g. full multi-MB log dumps rather than excerpts.
- **Trigram indexes are inherently larger per byte of source text** than token-based indexes (every 3-character window is a posting, vs. one posting per word). SQLite's own documentation shows a >5x size difference between `detail=full` and `detail=none` on large corpora; the practical implication here is: keep the trigram table scoped to genuinely code/log-shaped columns (raw log excerpts, code snippets), not the entire document, or the substring-search convenience stops being worth its footprint.
- **Trigram results aren't meaningfully rankable by relevance** — `bm25()` still runs on a trigram table but the "terms" are 3-character shingles, so a high-scoring match isn't necessarily a good match the way it is for word-token BM25. The tradeoff accepted here is presenting trigram hits as an unranked (or simply recency-ranked) secondary result set rather than trying to unify scoring across both tables.
- **Tuned `tokenchars` is a one-way door per table.** Once `incidents_fts` is built with `tokenchars '_.-:#/'`, every query and every future migration inherits that tokenization; changing it requires a full rebuild (see Migration Risk). The tradeoff is accepted because getting this wrong (default `unicode61` splitting `NullPointerException`-style identifiers into fragments, or `ECONNREFUSED` at the underscore) would be a much more damaging default for a corpus this technical.
- **Triggers add real correctness surface** (ordering rules, the `'delete'` command's exact-match requirement) that a duplicated-storage contentless table would sidestep. This is accepted because the "canonical source of truth + rebuildable derivatives" principle requires it — the alternative durably duplicates data that must then itself be treated as semi-canonical, which the project's architecture explicitly rejects.

### Portability & Offline

- FTS5 is compiled-in via a build flag (`SQLITE_ENABLE_FTS5`), not a given. Distro-packaged `libsqlite3` and some platform Python builds have shipped without it, and the stdlib `sqlite3` module in Python links whatever SQLite the Python build was compiled against — this is the single biggest portability risk for a feature that's supposed to "always work." Mitigate by taking a hard dependency on `pysqlite3-binary` (or an equivalent vendored/bundled build) so the application always runs against a known-modern SQLite (3.47+, ideally tracking current 3.53.x) with FTS5 guaranteed present, rather than trusting whatever `sqlite3` the host OS/Python happens to provide. Verify at startup with `PRAGMA compile_options` (look for `ENABLE_FTS5`) and fail loudly in CI/dev rather than silently in front of a user.
- Everything here is single-file and network-free by construction — no server process, no external index to keep alive — which is exactly the offline/local-first property the project needs. The FTS tables live in the same `.sqlite` file as the canonical `incidents` table, so a single file copy/backup captures both consistently (no cross-file consistency problem to reason about, unlike an attached/second database file).
- Rebuildability doubles as the portability safety net: because the FTS index is 100% derivable from canonical columns via `'rebuild'`, moving the SQLite file to a new machine with a different SQLite build, or recovering from a corrupted index, is always just "run the rebuild command" — never a data-loss event, since evidence files and canonical rows are untouched.
- Store a normalized plain-text extraction of evidence (log excerpts, stack traces already flattened to text) in canonical columns rather than only referencing the original evidence files on disk. This keeps FTS rebuild a pure in-database operation, independent of whether the original binary/log files are reachable at rebuild time (they remain the archival evidence of record; the extracted text is what's searchable).

### Complexity & Maintenance

- Total footprint is modest: one Alembic migration creating the two virtual tables + triggers, one `search` service module wrapping `MATCH`/`bm25()` queries, and one `rebuild_fts()` maintenance function exposed as both a CLI command and an MCP tool.
- **Virtual tables don't support arbitrary `ALTER TABLE`.** A schema change to `incidents_fts` (adding a column, changing `tokenchars`) means DROP + CREATE + full repopulation, not an in-place migration. Alembic revisions that touch FTS schema must therefore include a repopulation step (call `rebuild_fts()`), and should be written idempotently (`CREATE VIRTUAL TABLE IF NOT EXISTS`-style guards, or check-then-create) so re-running migrations against a partially-migrated DB is safe.
- **Trigger DDL must travel with canonical schema changes.** If a column is added to `incidents` that should be searchable, the trigger bodies and the FTS table's column list must be updated in the same Alembic revision as the source-of-truth change — treat them as one atomic unit, not two independently-evolving schemas, or drift silently breaks search for the new field.
- **Bulk imports should bypass triggers.** For large backfills (e.g., initial import of years of historical incidents), let the bulk insert into `incidents` run first without per-row trigger overhead disabled via a session flag, then do one batch `INSERT INTO incidents_fts SELECT ... FROM incidents` (or the `'rebuild'` command) — this avoids 10,000+ individual trigger-driven FTS writes during import.
- **Add a periodic integrity check**: `INSERT INTO incidents_fts(incidents_fts) VALUES('integrity-check')` as a scheduled/CLI maintenance task, catching silent drift or corruption before a user notices search "just isn't finding it."
- **Test the sync explicitly**: integration tests asserting FTS/canonical parity after insert/update/delete, a test that `rebuild_fts()` reproduces the exact same index from canonical data alone, and a test that the search code path degrades to "search unavailable" (not a crash) when FTS5 is detected absent.

### Migration Risk

- **FTS5 silently unavailable at runtime** (wrong SQLite build) — mitigated by pinning `pysqlite3-binary`/vendored SQLite and a startup `compile_options` check, not discovered ad hoc in production at 10,000-incident scale.
- **Trigger ordering bugs** (external-content DELETE/UPDATE triggers must remove old FTS entries before/using the old row's values, per SQLite's documented ordering requirement) silently degrade search recall without erroring — mitigated by CRUD-parity integration tests and the scheduled `'integrity-check'`.
- **FTS/canonical schema drift** across Alembic revisions if the two aren't changed together — mitigated by co-locating FTS DDL/trigger changes in the same migration as the underlying column change, plus a CI job that runs all migrations against an empty DB, then `rebuild_fts()`, then asserts row-count parity between `incidents` and both FTS tables.
- **SQLite version skew** across dev/CI/prod (older builds lacking `contentless_unindexed`, tokenizer v2/locale, or FTS5 entirely) — mitigated by the same bundled-SQLite pin used for portability; document a minimum of 3.47 and track current stable (3.53.x) in the dependency lockfile.
- **Any FTS-schema migration is inherently low-risk to roll back**, which is the payoff of this whole design: because the FTS tables hold zero data that isn't reconstructible from `incidents`, a downgrade that drops and recreates `incidents_fts`/`incidents_fts_trigram` can never lose information — worst case is a `rebuild_fts()` call after rollback. This is the concrete benefit of keeping FTS strictly derivative rather than choosing an architecture (e.g., contentless-as-primary) where the index itself would hold irreplaceable data.
- **Manual/ad-hoc FTS DML** (a developer hand-writing an `INSERT ... VALUES('delete', ...)` with slightly wrong old-column values) is the one way to actually corrupt the index without SQLite noticing immediately — mitigated by never allowing raw FTS DML outside the trigger definitions and the `rebuild_fts()` path; enforce via code review / a lint rule rather than relying on discipline alone.

---
Sources: [SQLite FTS5 Extension](https://sqlite.org/fts5.html), [Release History Of SQLite](https://sqlite.org/changes.html), [endoflife.date: SQLite](https://endoflife.date/sqlite)

---

## Local Embeddings

### Recommendation

**`BAAI/bge-small-en-v1.5`** (384‑dim, MIT license, 33M params) served through **`fastembed`** (Qdrant's ONNX-Runtime-based Python library) as the default local embedding backend.

Why this pairing wins for Engineering Memory specifically:

- **Simplicity over the last few points of MTEB score.** At 10,000-ish incidents, brute-force cosine similarity over 384‑dim float32 vectors is ~15MB of data and single-digit-millisecond search even in pure NumPy — dimension size and index sophistication are non-issues at this scale. The variable that actually matters is *operational simplicity and license cleanliness*, and bge-small wins there: MIT license, no `trust_remote_code`, no custom modeling code, one of the most widely deployed embedding models in production RAG systems today (it's fastembed's own default model).
- **fastembed over sentence-transformers as the primary runtime.** fastembed ships models pre-converted and pre-quantized to ONNX (int8 variant of bge-small is ~32MB on disk vs ~127MB fp32) and depends only on `onnxruntime` + `tokenizers` + `numpy` — no PyTorch/CUDA in the dependency tree. That's a materially smaller, more auditable, faster-to-install footprint for a tool that's supposed to degrade gracefully and stay easy to rebuild. `pip install fastembed`, no GPU, runs identically on the dev laptop and wherever this eventually deploys.
- **Query-only prefixing is a simpler mental model than symmetric prefixing.** BGE only wants an instruction prefix on the *query* side ("Represent this sentence for searching relevant passages: ..."); documents are embedded as-is. That's one code path to get right, versus E5's requirement to prefix *both* query and passage consistently (a common, silent source of bugs — see Migration Risk).

**Upgrade path (drop-in, same library):** if incident write-ups routinely include long stack traces/logs that a 512-token context window truncates, switch to `Alibaba-NLP/gte-base-en-v1.5` (768‑dim, Apache-2.0, 137M params, 8192-token context) — also natively supported by fastembed, so the trust_remote_code requirement that plain `sentence-transformers` loading needs is moot (fastembed serves a pre-traced ONNX graph, no custom Python model code executes at inference time). This is the single knob to turn for quality without changing library or architecture.

### Alternatives Considered

| Model | Dim | Params | License | Context | Notes |
|---|---|---|---|---|---|
| **bge-small-en-v1.5** ✅ chosen | 384 | 33M | MIT | 512 | Simplest, MIT, query-only prefix, fastembed default |
| gte-base-en-v1.5 | 768 | 137M | Apache-2.0 | 8192 | Best upgrade path; long context for logs/stack traces; no prefix needed at all |
| nomic-embed-text-v1.5 | 768 (Matryoshka: truncatable to 64–768) | 137M | Apache-2.0 | 8192 | Nice truncate-without-re-embed property; needs `search_query:`/`search_document:` prefixes; adds little over gte-base for this use case |
| intfloat/e5-small-v2 | 384 | 33M | MIT | 512 | Comparable size/license to bge-small; slightly better on code-heavy retrieval per CoIR benchmarks, but symmetric query/passage prefixing is an easy footgun |
| Qwen3-Embedding-0.6B | 32–1024 (flexible) | 0.6B | Apache-2.0 | 32K | Best-in-class quality/multilingual/code retrieval, but ~10-20x slower on CPU and ~10x the disk footprint — overkill for 10k incidents |
| google/embeddinggemma-300m | 768 (Matryoshka to 128) | 308M | Gemma usage license (gated, usage-restricted) | 2K | Strong for its size, but non-permissive license (must accept Google's usage terms, has prohibited-use policy) and no compelling quality win at this scale to justify the friction |
| all-MiniLM-L6-v2 | 384 | 22M | Apache-2.0 | 256 | The "boring, ubiquitous" 2021-era default; superseded in quality by BGE/E5/GTE on retrieval, but worth knowing as the most battle-tested fallback if fastembed's whole model catalog is ever unavailable |

Also considered and rejected: hosted/API embeddings (OpenAI, Voyage, Cohere) — categorically excluded by the "fully offline" requirement, not a fair comparison here.

### Tradeoffs

- **Quality vs. speed vs. context length**, not storage. At 10k incidents, storage and index-build time are trivial for every model above (largest option here, 768‑dim × 10k × 4 bytes ≈ 31MB). The real tradeoff axis is: smaller/faster models (bge-small, e5-small, MiniLM) truncate long documents at 256–512 tokens, while context-length-capable models (gte-base, nomic, Qwen3) avoid truncation but cost 4–20x more CPU time per document.
- **bge-small vs. gte-base in practice:** bge-small will silently truncate an incident write-up with a long embedded stack trace past ~512 tokens (roughly 350-400 words) unless you chunk. gte-base avoids that at the cost of ~4x slower inference (137M vs 33M params) — still comfortably fast enough for both interactive query embedding and full-corpus batch re-embedding at this scale (minutes, not hours).
- **Prefix convention complexity is a real, if small, cost.** BGE (query-only), E5/Nomic (symmetric query/document), GTE (none) each need different wrapper logic. Getting it wrong doesn't error — it just quietly degrades recall, which is worse than crashing because nothing will flag it in testing unless you specifically check retrieval quality.
- **License friction is a legitimate axis even for a personal tool.** MIT/Apache-2.0 models (bge, e5, gte, nomic, Qwen3) impose no usage restrictions. Gemma-family models carry Google's usage terms and a prohibited-use policy layered on top of the open weights — not a blocker for a private local tool, but an unnecessary complication with no compensating benefit here.

### Portability & Offline

- All candidate models, once downloaded once, run fully offline with no network calls at inference time — verify this by setting `HF_HUB_OFFLINE=1` after the first fetch to catch any accidental online dependency.
- **Prefer ONNX over raw PyTorch weights for the local runtime.** ONNX Runtime's CPU wheel has no CUDA/driver dependency, runs identically across Linux/macOS/Windows, and needs no compiler toolchain — important for a tool meant to be easy to set up on whatever machine the user is on.
- **Avoid `trust_remote_code` models where possible.** Loading gte-base or nomic directly via `sentence-transformers` executes author-supplied Python at model-load time, pulled from the HF Hub — a real (if easy to overlook) supply-chain/offline-reproducibility concern. Using fastembed's pre-exported ONNX graphs sidesteps this entirely: the custom modeling code only mattered at export time, not at your inference time.
- **Treat the model cache like the vector index — derived, not canonical.** Vendor or pin the exact HF revision, store it under a project-controlled cache directory, and don't rely on "whatever the ambient pip environment happens to have installed." This matches the project's existing rebuildability principle: SQLite + evidence are truth; the embedding model files are just another rebuildable artifact.

### Complexity & Maintenance

- **Centralize prefixing in one function.** Whatever model is chosen, write a single `embed(text: str, kind: Literal["query","document"]) -> np.ndarray` used everywhere embeddings are produced, so the query/document prefix convention (or lack thereof) is applied exactly once and can't drift between the indexing path and the search path.
- **Normalize before you store.** These models expect (or already output) L2-normalized vectors for cosine similarity; confirm normalization is applied by the library (fastembed does this for these models) rather than assuming it, since an un-normalized vector silently produces wrong-but-plausible-looking similarity scores.
- **Chunking is a separate design decision from model choice.** For incident docs with long logs/stack traces, decide once whether to (a) rely on gte-base's 8192-token context to avoid chunking almost entirely, or (b) chunk sections (problem/symptoms/root-cause/solution/lessons) independently and store one vector per section. Either is fine; don't let it become an ad hoc per-incident inconsistency.
- **Dependency footprint matters for a "must always degrade gracefully" system.** fastembed's install (`onnxruntime`, `tokenizers`, `numpy`) is far smaller and more stable than `sentence-transformers`' default install (which pulls in `torch`, `transformers`, `huggingface_hub`, `safetensors`, often `pillow`/`scikit-learn`) — fewer transitive dependencies to patch, faster fresh installs, and a much easier story for "the AI/embeddings layer failed to import, fall back to lexical search" error handling, since the failure surface is smaller.
- Keep `sentence-transformers` in mind only as a fallback path — the reference implementation with the widest model coverage — for the rare case a needed model isn't in fastembed's catalog, or if fine-tuning is ever wanted.

### Migration Risk

- **Pin an exact model revision, not just a name.** Store `model_name` + `model_revision` (HF commit hash) + `embedding_dim` in configuration and, critically, as columns alongside every stored embedding row. Never trust "whatever is currently installed" as the implicit source of truth — a repo can technically update weights in place even under the same tag.
- **Embeddings from different model versions are not comparable, and nothing will tell you that at query time if you don't check.** A change to weights, tokenizer, pooling, or ONNX export/quantization settings shifts the vector space; mixing old and new embeddings in one similarity search produces meaningless-but-plausible scores, silently. Gate semantic search on a version check (`if stored_revision != configured_revision: warn / block / trigger rebuild`), the same way you'd gate on an unapplied Alembic migration.
- **Re-embedding is cheap at this scale — treat it as routine, not scary.** Even the 137M-parameter option can fully re-embed 10,000 incidents from canonical SQLite text in low single-digit minutes on CPU. This is exactly what the "everything except SQLite + evidence is rebuildable" principle is for: provide a `rebuild-embeddings` command that wipes and regenerates the vector store from canonical text whenever the model changes, and don't build any cleverness for in-place vector migration — it isn't needed at this scale and adds risk for no benefit.
- **The realistic trigger for migration is "we chose to upgrade for quality," not "the vendor silently changed something."** bge-small/gte-base/e5-small are mature, slow-moving public artifacts; still pin the revision hash defensively, but the operational risk here is low.
- **Matryoshka-capable models (nomic, EmbeddingGemma, Qwen3) offer a future lever, not a current need:** their embeddings can be truncated and re-normalized to a smaller stored dimension without re-running the model on raw text. Irrelevant at 10k incidents; worth remembering only if the stated scale target ("not millions") ever changes.

**Sources:**
- [BAAI/bge-small-en-v1.5 · Hugging Face](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [BGE-M3 — BGE documentation](https://bge-model.com/bge/bge_m3.html)
- [Alibaba-NLP/gte-base-en-v1.5 · Hugging Face](https://huggingface.co/Alibaba-NLP/gte-base-en-v1.5)
- [nomic-ai/nomic-embed-text-v1.5 · Hugging Face](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)
- [Nomic Embed Matryoshka | Nomic News](https://www.nomic.ai/news/nomic-embed-matryoshka)
- [intfloat/e5-small-v2 · Hugging Face](https://huggingface.co/intfloat/e5-small-v2)
- [Qwen/Qwen3-Embedding-0.6B · Hugging Face](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Qwen3 Embedding blog — QwenLM](https://qwenlm.github.io/blog/qwen3-embedding/)
- [google/embeddinggemma-300m · Hugging Face](https://huggingface.co/google/embeddinggemma-300m)
- [Welcome EmbeddingGemma — Hugging Face blog](https://huggingface.co/blog/embeddinggemma)
- [FastEmbed — Qdrant documentation](https://qdrant.tech/documentation/fastembed/)
- [FastEmbed article — Qdrant](https://qdrant.tech/articles/fastembed/)
- [fastembed Supported_Models.ipynb — GitHub](https://github.com/qdrant/fastembed/blob/main/docs/examples/Supported_Models.ipynb)
- [CoIR: A Comprehensive Benchmark for Code Information Retrieval Models](https://arxiv.org/html/2407.02883v3)
- [Sentence Transformers v6.0 release notes context — GitHub releases](https://github.com/UKPLab/sentence-transformers/releases)
- [gte-base-en-v1.5 trust_remote_code issue — GitHub](https://github.com/huggingface/sentence-transformers/issues/3717)

---

## Vector Index

### Recommendation

**Start with a plain numpy brute-force cosine table; adopt `sqlite-vec` only if/when brute force stops being "fast enough."** Do not reach for FAISS, usearch, LanceDB, or Chroma at this scale.

Concretely:

1. **Storage (unconditional, works today):** embeddings live as a `BLOB` column (`float32` bytes, e.g. via `np.ndarray.tobytes()`) on a `chunk_embeddings` table keyed to the canonical `incident_id`/`chunk_id`, alongside the embedding model name + version. This table is 100% derivable from canonical text — delete it and it's just a re-embedding job away from existing again.
2. **Query (v1 implementation):** on startup (or lazily, cached), load all rows into one in-memory `numpy` matrix (`N × dim`, L2-normalized). A query embedding becomes a single `matrix @ query_vec` dot-product (cosine, since vectors are normalized) plus `np.argpartition` for top-k. At 10,000 rows × 1536 dims that's a ~60MB matrix and a **single-digit-to-low-double-digit millisecond** query — no index structure, no ANN approximation, exact results every time.
3. **Upgrade path (only if needed):** if row count or dimensionality grows enough that the brute-force scan becomes noticeable (rough rule of thumb: comfortably fine to 50–100k rows before it's worth revisiting), swap in **`sqlite-vec`'s `vec0` virtual table**, which stores its shadow tables *inside the same SQLite file* — so the "one file = canonical backup" property survives the upgrade unchanged. Wrap both behind one small `VectorIndex` interface (`upsert`, `search(query_vec, k)`, `rebuild()`) so this swap doesn't touch calling code.

Both tiers are rebuildable from canonical text + the recorded embedding-model version, both degrade to "vector search unavailable, fall back to FTS5 lexical" if numpy/the extension/the model is missing, and neither introduces a second system of record.

### Alternatives Considered

| Option | Verdict | Why |
|---|---|---|
| **numpy brute-force table** | **Recommended (v1)** | Zero new dependencies (numpy is already needed for embeddings), exact (not approximate) results, trivially rebuildable, and fast enough at this scale — 10k rows is a rounding error for a BLAS matmul. |
| **sqlite-vec** (`vec0`) | **Recommended (upgrade path)** | Successor to sqlite-vss; pure C, no dependencies, prebuilt wheels for Linux/macOS/Windows, runs in-process as a SQLite loadable extension. Vectors + shadow tables live in the *same* `.sqlite` file. Current release **0.1.9** (Mar 2026), with 0.1.10-alpha builds in flight — it is explicitly **pre-1.0**, so SQL API / on-disk format may still change before a 1.0 stable. |
| **sqlite-vss** | **Rejected — deprecated** | Original author has moved development to sqlite-vec; sqlite-vss depended on a vendored Faiss build in C++, only reliably built on Linux/macOS, and is not actively maintained. No reason to adopt it new in 2026. |
| **FAISS** | Rejected — overkill | Mature and fast, but designed for millions–billions of vectors with IVF/HNSW/PQ. At 10k rows it brings a large native (BLAS/OpenMP) dependency for no accuracy or speed benefit over brute force, and its index lives in a **separate file** (`faiss.write_index`/`read_index`) you must keep in sync with the SQLite file by hand — plus you must maintain your own id→metadata mapping since FAISS only knows integer ids. |
| **usearch** | Rejected — overkill | Legitimately lightweight (single-file HNSW index, Apache-2.0, actively released through mid-2026, markets itself as faster than FAISS at small/medium scale), but it's still a **second index file and format** outside SQLite, for a scale (10k rows) where an index structure buys nothing over exact brute force. Reasonable fallback if the project ever needs real ANN at 100k+ rows. |
| **LanceDB** | Rejected — wrong storage model | Genuinely embedded (in-process, no server) and well regarded in 2026 for multimodal/edge use, but its canonical unit is the **Lance columnar format** — a directory of versioned files, not a row in your SQLite DB. Adopting it means a second, competing "source of truth" for the same data, which directly conflicts with the "SQLite + evidence files are canonical" principle. Also a Rust-toolchain dependency for a problem numpy already solves at this size. |
| **Chroma (embedded / `PersistentClient`)** | Rejected — heaviest footprint | Runs in-process without a server, but its "full client" pulls in `onnxruntime`, `duckdb` (or its own SQLite), `grpcio`, `pydantic`, `hnswlib`, etc. — a large, sometimes fragile dependency chain (recurring onnxruntime version-pin install failures across platforms/Python versions in the wild). It also *wants to be* your primary datastore (its own embedded SQLite/DuckDB), which duplicates rather than complements the canonical DB. Far more machinery than 10k rows justifies, and you don't need its built-in embedding functions since you bring your own model. |

### Tradeoffs

- **numpy brute-force**: simplest possible correct answer; cost scales linearly with rows × dims (fine here, would need revisiting well before 1M rows); no ANN recall loss since it's exact.
- **sqlite-vec**: near-numpy simplicity plus SQL-level `MATCH`/KNN queries and the ability to push filtering into SQLite; costs you a pre-1.0 API that may still shift, and a compiled extension you must load per-platform.
- **FAISS/usearch**: best-in-class ANN performance at real scale, but that performance is irrelevant at 10k rows, and both add an out-of-band index file plus your own id↔row bookkeeping.
- **LanceDB/Chroma**: best when you want a full "vector database" product (versioning, multimodal blobs, built-in embedding pipelines) — solving problems this project doesn't have, at the cost of a second canonical store and materially heavier install footprint.

### Portability & Offline

- **numpy**: nothing to back up beyond the SQLite file itself — embeddings are already BLOB columns in it. No server, no binary extension, no network calls; works fully air-gapped by construction.
- **sqlite-vec**: still a single `.sqlite` file for backup purposes (shadow tables live inside it), but the loadable extension binary (`vec0.{so,dylib,dll}`) must be vendored/pinned per OS/arch for fully offline installs — a small, manageable addition (~1MB), unlike sqlite-vss's old C++/Faiss build chain.
- **FAISS/usearch**: index lives in a *separate* file next to the SQLite DB — "single-file backup" becomes "two-artifact backup kept in sync," and FAISS's wheel is comparatively large (BLAS/OpenMP bundled). Both are fully offline-capable once installed (no network calls at query time).
- **LanceDB**: backup unit becomes a directory tree of versioned Lance files rather than one file — offline-capable, but no longer "one file = everything."
- **Chroma**: heaviest offline footprint — onnxruntime binaries plus its own embedded DB files must all be present and versioned together; still no network dependency at runtime, but the largest thing to vendor and the most files to keep consistent.

### Complexity & Maintenance

- **numpy**: essentially zero new maintenance surface — it's application code you fully own, not a dependency with its own release cadence or breaking changes.
- **sqlite-vec**: one small pip dependency (`sqlite-vec`) plus `conn.enable_load_extension()` boilerplate; being pre-1.0 means watching for breaking SQL/storage-format changes across releases, but the project's scope is narrow enough that upgrades are low-effort to verify.
- **FAISS**: stable, mature API for the basic index types, but it's a large C++ project with its own build/version quirks (GPU variants, threading config) that add no value at this scale — maintenance cost without a corresponding benefit.
- **usearch**: newer, smaller community than FAISS; single-file simplicity is nice, but it's still a distinct dependency/format to track for no scale-driven need.
- **LanceDB**: actively evolving storage format (e.g., Lance File Format 2.2 shipped in 2026) — schema-evolution and versioning features are more sophistication than a 10k-row incident store needs, plus a Rust toolchain in the dependency graph.
- **Chroma**: the most moving parts of any option here — its own client/server abstraction even in "embedded" mode, its own migrations, and a default embedding-model runtime (onnxruntime) you'd immediately disable in favor of your own model. Highest ongoing maintenance for a feature set you won't use.

### Migration Risk

The project's core mitigant applies to every option: because embeddings (and the index built over them) are always rebuildable from canonical text plus the recorded embedding-model version, **losing or discarding any vector index is a re-embedding job, never data loss.** That reframes "migration risk" as "how annoying is the rebuild/swap," not "can we lose data":

- **numpy → sqlite-vec later**: lowest risk of all migrations — same canonical BLOB column, just a different query engine on top; no schema change needed if you designed the storage table up front.
- **sqlite-vec pre-1.0 churn**: the real risk here is the extension's own API/storage format changing before 1.0 — mitigated by treating `vec0` strictly as a *rebuildable secondary index* (never the only place an embedding lives) and pinning the extension version in requirements.
- **FAISS/usearch index-file drift**: library major-version bumps occasionally change on-disk index formats; low risk in practice because you'd always rebuild the index from source vectors rather than trying to forward-migrate an old index file.
- **LanceDB**: adopting it means accepting a second canonical-ish store outside SQLite — the largest architectural migration risk of the group, and the hardest to unwind later since incident data would be split across two storage systems.
- **Chroma**: similar risk profile to LanceDB — its embedded SQLite/DuckDB store becomes a parallel system of record; migrating away later (e.g., a breaking client-API change, since Chroma's hosted/self-host split has moved fast historically) is more painful precisely because its API is more opinionated than "vectors in a table."

**Bottom line:** for 10,000-incident scale, a dedicated vector database is unambiguously overkill. A numpy brute-force table is the right v1 — it's exact, dependency-free, and trivially rebuildable — with `sqlite-vec` as the natural, low-risk upgrade precisely because it keeps the "one SQLite file is canonical and portable" property intact even as the index gets smarter.

---

## Hybrid Retrieval & Reranking

### Recommendation

**Use weighted linear fusion over four parallel signals, with an exact-identifier short-circuit, hard metadata pre-filtering, relationship expansion as a low-weight secondary signal, and no cross-encoder reranker in v1.** Every stage runs in-process in the FastAPI backend against the same SQLite connection — no separate vector-DB service, no ML training pipeline. Fusion weights live in a small versioned config that ships with the repo, and every search response carries a per-signal score breakdown so "why did this rank highly" is answerable from the response payload alone, not from inspecting logs.

**Concrete pipeline:**

1. **Query understanding.** Parse the raw query into: (a) explicit facets (technology/project/environment — either UI-selected chips or `key:value` query tokens), (b) candidate exact identifiers extracted via a small, config-driven regex library (error codes like `ORA-00001` or `ECONNREFUSED`, ticket/incident IDs like `INC-1234`, stack-trace class names, k8s resource names, IPs, HTTP status+path pairs), (c) the remaining free text for lexical/semantic search.
2. **Candidate generation (parallel, all pre-filtered by facets via SQL `WHERE`):**
   - **Exact match:** lookup against a dedicated `incident_identifiers` table (populated at ingestion time by the same regex library, so extraction is deterministic and rebuildable) plus an FTS5 `trigram` tokenizer index for substring matches SQLite's default tokenizer would miss. Returns a small, high-confidence set with match spans.
   - **Lexical:** FTS5 (`unicode61` + `porter` tokenizer) query over title/body/tags, ranked with SQLite's built-in `bm25()`, top ~100 candidates.
   - **Semantic:** embed the query with whatever embedding backend is configured, KNN search against a `sqlite-vec` (`vec0`) virtual table storing per-chunk embeddings, top ~100 candidates by cosine similarity. Skipped entirely if no embedding model is available — see degradation below.
   - **Relationship expansion:** for the top ~5 seeds from the above, pull directly-linked incidents from an explicit `incident_relations` table (same-root-cause, same-service, explicit "related to" links authored by the user or an AI summarizer). These get a small fixed slot count and a decayed score, not full ranking weight — this is a recall/serendipity aid, not a primary relevance signal.
3. **Normalization.** Min-max normalize each signal's raw scores to [0,1] *within the current candidate set*. This is the simplest thing that lets differently-scaled signals (BM25 unbounded, cosine [-1,1] or [0,1], boolean exact match) combine sensibly, and it's the easiest to explain in a UI ("this was the 2nd-best lexical match, normalized to 0.81"). Z-score normalization is called out as a documented fallback if min-max proves too sensitive to outlier candidate sets in practice.
4. **Weighted linear fusion.**
   ```
   fused_score = w_exact   * exact_match_score        (default 0.35, near-binary)
               + w_lexical * bm25_norm                (default 0.30)
               + w_vector  * cosine_norm               (default 0.25)
               + w_related * relationship_decay_norm   (default 0.10)
   ```
   Weights live in a checked-in config (`ranking.weights.json` or a `settings` table with a versioned `weights_profile_id`), are user-editable, and **automatically renormalize when a signal is unavailable** — e.g., no embedding model configured ⇒ `w_vector` redistributes proportionally to `w_lexical`/`w_exact` rather than silently zeroing out part of the score mass.
   Offer **RRF as a selectable fusion mode** alongside weighted linear (same candidate lists feed either fusion function) — it's ~30 lines of code, needs no normalization step, and gives users a scale-invariant option if they distrust hand-tuned weights. This satisfies "configurable" cheaply without committing to it as the sole mechanism.
5. **Exact-match short-circuit.** If a strong exact identifier match is found (e.g., the query contains a ticket ID or error code that matches verbatim), those incidents are pinned to the top regardless of fused score — for engineering-incident search, an exact error-code match is near-certain relevance and users will distrust a system that buries it under a "more semantically similar" result.
6. **Optional rerank stage (v2, off by default).** Architected as a pluggable post-fusion step that takes the fused top-K (~20–30) and reorders via a local cross-encoder — see Alternatives Considered for why this isn't in v1.
7. **Explainability payload.** Every result returned by the API carries:
   ```json
   {
     "incident_id": "...",
     "fused_score": 0.78,
     "weights_profile": "default-v3",
     "signals": {
       "exact_match": {"hit": true, "spans": ["INC-4821"]},
       "lexical": {"bm25_raw": 12.4, "normalized": 0.81, "rank": 2},
       "vector": {"cosine_raw": 0.71, "normalized": 0.66, "rank": 5},
       "relationship": {"via": "INC-4790", "edge_type": "same_root_cause", "decay": 0.4}
     },
     "degraded": {"vector_search": false}
   }
   ```
   The frontend renders this as an expandable "why this ranked highly" row per result. The `weights_profile` id (not just the raw weights) is stamped onto the result so a search performed before a weight change stays explainable after weights are later edited.
8. **Graceful degradation.** If the embedding model/vector index is unavailable: skip step 2's semantic branch, renormalize weights, set `degraded.vector_search = true` in the response so the UI can show a banner. If FTS5 itself were somehow unusable, fall back further to a plain `LIKE` substring scan — search must never fully fail, only get worse.

### Alternatives Considered

**Score fusion — RRF vs weighted linear vs learned fusion.**
- *Reciprocal Rank Fusion*: rank-based (`score = Σ 1/(k + rank)`, k≈60), needs no score normalization, robust to wildly different score distributions across retrievers, and is the de facto default in Elasticsearch/OpenSearch hybrid search. Downside: it discards magnitude — a lexical match that barely crosses the BM25 threshold at rank 1 gets identical credit to an overwhelming match at rank 1, and per-source weighting is coarser and less intuitive to present to a user than "72% lexical, 66% semantic."
- *Weighted linear fusion*: preserves magnitude, weights map directly onto an explainable UI ("lexical contributed 0.30 × 0.81"), and is what was chosen as the primary mode. Its weakness is normalization sensitivity — min-max scores are relative to whatever else is in that query's candidate set, so the same document's normalized lexical score can shift slightly query to query depending on competition.
- *Learned fusion (LTR / small logistic regression over signal features)*: highest theoretical ceiling if there's enough labeled relevance/click data, but this is a single-user local tool — realistic query volume (tens to low hundreds per week) will never produce a training set large enough to avoid overfitting, adds a training/maintenance pipeline that contradicts the project's "everything but SQLite + evidence is rebuildable and inspectable" principle, and a learned weight vector is materially harder to explain to the user than a hand-set one. **Rejected for v1**; noted as a plausible future evolution (a logistic regression over the same 4 features is really just "weighted linear fusion with learned instead of hand-set weights" and would slot into the same architecture without a redesign) if usage logs ever accumulate into the thousands.

**Local cross-encoder reranker.** Candidates considered: `cross-encoder/ms-marco-MiniLM-L-6-v2` (small, fast, CPU-friendly) and `BAAI/bge-reranker-v2-m3` (stronger, larger, multilingual). At this scale — 10k+ incidents, per-query candidate pools of ~100 before fusion and ~20–30 after — BM25 + a decent embedding model already separate relevant from irrelevant results well; the marginal ranking-quality gain from a cross-encoder pass is unlikely to be worth the added CPU latency (tens to a couple hundred ms even for a small model), an extra model download/dependency, and another moving part to keep rebuildable and versioned. **Deferred to v2** as an optional, config-gated stage with its own latency budget, added only if real usage shows the fused ranking is insufficient — the pipeline is architected so it can slot in after step 5 without touching the fusion contract.

**Vector backend.** Considered `sqlite-vec` (in-process SQLite extension, `vec0` virtual tables, no separate server), plain NumPy brute-force cosine similarity over embeddings loaded from a regular table, FAISS, and an external vector DB (Qdrant/Chroma/pgvector). At 10k incidents × a handful of chunks each (well under 100k–1M vectors), brute-force is already fast enough, but `sqlite-vec` was chosen as the default because it keeps everything in the one SQLite file (matching the "SQLite + evidence files are the only non-rebuildable state" principle), supports metadata-filtered KNN natively in SQL, and needs no additional service. An external vector DB was rejected outright — it would introduce a second source of truth and a network hop for what is explicitly a local-first, comfortably-sub-million-row workload.

**Pure semantic-first-with-lexical-fallback vs true hybrid fusion.** A simpler design (try semantic search, fall back to lexical only if embeddings are unavailable) was considered and rejected: exact identifiers and error codes are common and important in incident search, and semantic embeddings are often *worse* than lexical/exact matching for them (a rare error code has no useful semantic neighborhood). True parallel hybrid fusion, not a fallback chain, is needed to get both kinds of query right in the same pass.

### Tradeoffs

- **Weighted linear's normalization brittleness vs RRF's loss of magnitude.** Chosen mode (weighted linear) is more explainable but its normalized scores are candidate-set-relative; documented and mitigated by offering RRF as an alternate mode rather than pretending normalization is perfectly stable.
- **Explainability vs sophistication.** Learned fusion would likely rank marginally better once enough data existed, but a hand-tunable, inspectable weight vector was prioritized over a marginal quality gain the user couldn't audit — consistent with the project's broader "explainable, rebuildable" ethos.
- **Reranker quality vs latency/complexity.** A cross-encoder would probably improve ranking of the hardest, most semantically-nuanced queries, but at 10k-incident scale that gain is speculative while the latency, dependency weight, and added failure surface are certain. Deferred rather than dropped.
- **Hard metadata filters vs soft boosts.** Hard `WHERE`-clause filtering on explicitly-selected facets (technology/project/environment) is simple and predictable but can zero out a good result if the user mis-tags or under-specifies a facet; soft boosting would be more forgiving but harder to explain ("why did an incident from the wrong project show up?"). Hard filters were chosen for explicit user-selected facets; soft boosting is reserved for facets inferred from free text.
- **Relationship expansion's recall vs relevance dilution.** Pulling in linked incidents increases serendipitous recall ("here's the related outage from last quarter") but if weighted too high it dilutes the primary ranking with tangentially-related noise — mitigated by a low default weight and a fixed small slot count rather than letting it compete freely for rank.

### Complexity & Maintenance

- All fusion logic lives in one small, isolated module (e.g. `app/search/fusion.py`) with pure functions over plain score dicts, unit-tested against synthetic candidate sets (including edge cases: empty vector results, empty lexical results, ties, all-signals-degraded) — no need to spin up SQLite or a model to test ranking math.
- No additional runtime services: FTS5 and `sqlite-vec` both run as extensions/virtual tables inside the same SQLite connection the rest of the app already uses. Nothing external to deploy, monitor, or keep in sync.
- Weight profiles are a version-controlled JSON/YAML file (or a `settings` table seeded from one), diffable in code review like any other config — not a black-box model artifact.
- Embedding rows store the embedding-model id/version alongside the vector, and identifier rows store the regex-library version used to extract them, so both are self-describing and rebuildable: re-embedding or re-extracting the whole 10k-incident corpus is a scripted, idempotent maintenance command (`scripts/rebuild_index.py`), not a one-off migration — consistent with "everything but SQLite + evidence files is rebuildable."
- Recommended concrete versions (as of Sep 2026): Python's bundled `sqlite3` on 3.12/3.13 is usually new enough, but pin `pysqlite3-binary` if the target OS ships an older system SQLite, to guarantee FTS5 trigram tokenizer support and `load_extension` for `sqlite-vec`. Use the current stable `sqlite-vec` PyPI package (0.1.x line) loaded via `sqlite_vec.load(conn)`. For a default local embedding model, a small CPU-friendly model such as `BAAI/bge-small-en-v1.5` (or a newer compact 2025/2026 release like `Qwen/Qwen3-Embedding-0.6B` if quality needs outweigh the larger footprint) run via `sentence-transformers`; an opt-in cloud embedding path (e.g., Voyage AI, Anthropic's recommended embeddings partner) can be added later purely as an enhancement, never a requirement, preserving offline operation.

### Migration Risk

- **Embedding model changes** (swapping models, changing dimensionality) require full re-embedding of the corpus. At 10k incidents this is cheap (low minutes on CPU), but it must be *detected*, not silent: every embedding row is tagged with the model id/version, and index-build tooling refuses to mix embedding spaces — a dimension or model-id mismatch triggers a full rebuild rather than corrupting the KNN index.
- **FTS5 schema/tokenizer changes** (e.g., adding the trigram tokenizer, changing stemming) require a full FTS index rebuild (`INSERT INTO fts(fts) VALUES('rebuild')` or drop/recreate). This is scripted as an idempotent maintenance command that can be re-run safely, since it will be needed again whenever tokenizer configuration evolves.
- **`sqlite-vec` version upgrades**: the `vec0` virtual table's on-disk format can change across the extension's major versions. Mitigation: pin the extension version explicitly, test upgrade paths in CI before rollout, and — critically — never store embeddings *only* inside the `vec0` index. Keep raw embedding vectors in a plain table as the rebuildable source of truth, so the vector index itself can always be dropped and rebuilt trivially if the extension format changes.
- **Fusion algorithm/weight changes** are low-risk going forward (pure config), but changing the algorithm itself (e.g., switching the default from weighted-linear to RRF) changes what old explainability records mean. Mitigation: every search result/log row stamps the `weights_profile_id` (and fusion mode) actually used at query time, so historical "why did this rank highly" explanations remain accurate even after defaults change later.
- **Identifier-extraction regex changes** require re-scanning existing incidents to repopulate `incident_identifiers` — scripted as a rebuildable maintenance task (like re-embedding), not a one-way migration, so improving error-code/ticket-ID pattern coverage later doesn't strand old incidents with stale extractions.

---

# Retrieval Evaluation

## Recommendation

Build a **small, hand-labeled, deterministic benchmark** that lives in the repo as data (not prose), and a **thin pytest harness** that runs it against the real retrieval pipeline (FTS5 lexical → optional vector rerank). This is evaluation-as-regression-test, not an academic IR study: the goal is "did this change make search worse," not "what is our absolute nDCG on a standard collection."

**Corpus (`tests/retrieval/fixtures/corpus.yaml`)**
A synthetic-but-realistic set of ~150–250 incidents, small enough to hand-curate, covering every category the project cares about plus deliberately confusable pairs. Synthetic (not pulled from the user's real incident log) so it's safe to commit to git and share — the private, real log can later get its own *gitignored* supplementary benchmark file for personal calibration, but that's optional and never required for CI.

Each incident carries the same structured fields the real schema will have, so the fixture doubles as a schema smoke test:

```yaml
- id: inc-0031
  title: "CUDA OOM when two training jobs share one GPU"
  body: |
    torch.cuda.OutOfMemoryError during model.fit(); nvidia-smi shows
    two python processes on GPU0. Root cause: no MPS/exclusive mode,
    default memory fraction unset.
  tags: [cuda, pytorch, gpu, oom]
  environment: {os: ubuntu-22.04, cuda: "12.4", gpu: "RTX 4090", runtime: bare-metal}

- id: inc-0032
  title: "CUDA OOM inside Docker container, host GPU has headroom"
  body: |
    Same OutOfMemoryError signature as inc-0031 but nvidia-smi on the
    host shows plenty of free memory. Root cause: --gpus flag missing
    memory limit reconciliation with nvidia-container-runtime cgroup.
  tags: [cuda, docker, gpu, oom, nvidia-container-runtime]
  environment: {os: ubuntu-22.04, cuda: "12.4", gpu: "RTX 4090", runtime: docker}
```

`inc-0031`/`inc-0032` are a **confusable pair**: near-identical symptom text, different root cause and environment. This pattern repeats across all requested categories — e.g. ROS2 "topic silently not delivered" from a QoS reliability mismatch vs. from a DDS discovery/multicast network issue; a C++ segfault from an out-of-bounds `std::vector` access on x86_64 vs. the same code pattern on an ARM embedded target with a different ABI; a Git "detached HEAD, changes seem to vanish" incident vs. a "force-push overwrote branch" incident; a Postgres connection-pool exhaustion vs. a long-running-transaction lock-wait incident; a CMake "wrong compiler picked up" vs. a Bazel "stale remote cache" build-system incident; an OpenCV camera-calibration distortion bug vs. a lighting/exposure false-positive in a CV pipeline. Aim for at least one confusable pair per required category (CUDA, ROS2, Docker, C++, Python, Linux, networking, Git, databases, embedded, computer vision, build systems) — that's ~12–15 pairs, ~25–30 incidents, plus enough filler incidents (~120–200) to make retrieval actually have to discriminate rather than match by elimination.

**Queries (`tests/retrieval/fixtures/queries.yaml`)**
~40–60 queries, each with graded-but-simple relevance judgments and, for the environment cases, an explicit preference assertion:

```yaml
- query: "training job crashes with CUDA out of memory, works fine alone"
  relevant: [inc-0031]          # highly relevant
  partial:  [inc-0032]          # same symptom family, weaker match
  category: cuda

- query: "CUDA OOM but nvidia-smi on host shows free memory, running in a container"
  relevant: [inc-0032]
  environment: {runtime: docker}
  must_outrank:                 # pairwise environment-awareness assertion
    - {preferred: inc-0032, over: inc-0031}
```

**Metrics computed per query, then averaged (and reported per-category so a regression in, say, ROS2 doesn't hide inside a flat aggregate):**

| Metric | Definition | K |
|---|---|---|
| Precision@K | relevant hits in top K / K | 3, 5 |
| Recall@K | relevant hits in top K / total relevant for that query | 5, 10 |
| MRR | 1 / rank of first relevant hit (0 if none in top 10) | — |
| Pairwise env-accuracy | fraction of `must_outrank` pairs where `preferred` ranks above `over` | — |

Pairwise env-accuracy is the concrete, automatable form of "qualitative environment-aware ranking" — instead of eyeballing whether a mismatched result "feels" less relevant, assert a strict rank ordering on curated pairs. This turns the fuzziest requirement into a real regression gate.

**Harness (`tests/retrieval/test_retrieval_eval.py`, runnable also as `scripts/eval_retrieval.py` for a human-readable report):**

1. Load the fixture corpus into a throwaway SQLite DB (in-memory or tmp file) through the real ingestion path (same FTS5 table build, same chunking) so the test exercises actual code, not a mock.
2. Run the benchmark in **two profiles**: `hybrid` (BM25 + embeddings + rerank) and `lexical_only` (embeddings/vector index forced off). This directly encodes the system's "must degrade gracefully" requirement — lexical-only has its own, lower but non-trivial, threshold gate, so a regression that silently breaks the FTS5 fallback path is caught even though the primary hybrid profile still passes.
3. For each query, call the actual `/search` retrieval function, compute the metrics table above, and assert against a small **thresholds file** (`tests/retrieval/thresholds.yaml`, e.g. `hybrid: {p@5: 0.75, mrr: 0.70, pairwise_env: 0.90}`, `lexical_only: {p@5: 0.55, mrr: 0.50, pairwise_env: 0.60}`) committed alongside the fixtures so threshold changes show up in code review, not silently.
4. Print a full per-query, per-category table on failure (or via a `--report` flag) so a human can see *which* confusable pair regressed, not just that "MRR dropped."

If embeddings require a network call at eval time, cache the fixture embeddings as small local vectors checked into the repo (the corpus is tiny) so the test is offline and deterministic — consistent with the project's own rebuildability principle: the eval's own index must be trivially rebuildable from the fixture text, and must not depend on live AI availability to run.

**When it runs:** locally on demand (`pytest tests/retrieval` / `make eval-retrieval`), in CI on any PR touching search, ranking, embedding, or ingestion code, and manually re-baselined (not silently) whenever the embedding model version or FTS5 tokenizer configuration changes.

## Alternatives Considered

- **Academic IR test collections (BEIR, MS MARCO, TREC-style pooling).** Wrong domain (web/QA passages, not personal engineering incidents), wrong scale assumptions (built for corpora orders of magnitude larger, with pooled multi-annotator judgments), and would add nothing this project can act on — no incident in this system will ever look like a TREC judgment.
- **LLM-as-judge relevance scoring** (ask Claude to rate each retrieved result's relevance to the query). Attractive for scaling label creation, but makes the *ground truth* itself depend on AI availability and is non-deterministic run-to-run — a poor fit for a regression gate in a system whose explicit design principle is "must degrade gracefully if AI is unavailable" and where SQLite + evidence files, not model output, are the source of truth. Reasonable as an *adjunct* — e.g., to draft candidate query/relevance pairs for a human to confirm — but not as the pass/fail signal.
- **nDCG with fully graded (0–3) relevance.** More expressive than P@K/Recall@K/MRR, but the labeling overhead (calibrating 4 relevance grades consistently across 50+ queries, alone, without a second annotator) isn't worth it at this scale. The two-tier `relevant`/`partial` scheme above gets most of the benefit; nDCG is a reasonable future upgrade if the benchmark grows past ~150 queries.
- **Online/production eval** (click-through, thumbs-up on results, dwell time). The right long-term signal, but this is a solo-user, local-first tool bootstrapping from zero incidents — there's no traffic to learn from for a long time, and instrumenting it adds its own complexity (event logging, privacy handling of a personal incident log) before there's data to justify it.
- **A/B testing ranking variants.** No statistical power with one user and no concurrent traffic; not applicable.
- **Adopting an existing metrics library (`ranx`, `pytrec_eval`, `ir_measures`) instead of hand-rolling P@K/Recall@K/MRR.** Worth doing for the metric *computation* (they're well-tested, handle edge cases like ties and empty relevant sets correctly) — recommended as a small dependency rather than reimplementing. The fixture format, harness wiring, and pairwise-environment assertion are still custom regardless, since no off-the-shelf library models "environment-mismatch penalty."

## Tradeoffs

- **Single-annotator bias.** The user labels their own benchmark, so relevance judgments reflect one person's mental model. Mitigation: write `relevant`/`must_outrank` labels *before* looking at what the system actually returns (blind labeling), and revisit labels periodically rather than tuning them to make the system look good.
- **Small-N noise.** With ~40–60 queries, individual metric values swing on a handful of query outcomes; a single confusable-pair fix can move aggregate MRR noticeably. Treat these numbers as **relative, run-over-run regression signals**, not absolute quality claims — the per-category and per-pair breakdown matters more than the aggregate score.
- **Binary/two-tier relevance vs. full grading.** Simpler to maintain, cheaper to extend, but can't distinguish "somewhat relevant" from "very relevant" within the `partial` bucket. Acceptable at this scale; would need revisiting if the benchmark grows substantially.
- **Automated metrics vs. genuine judgment quality.** P@K/Recall@K/MRR and pairwise env-accuracy catch *regressions* on cases the author already thought of. They won't catch a genuinely new class of environment-confusion bug that no existing fixture entry represents. Treat every real search mistake the user notices while dogfooding as a prompt to add a new fixture pair — the benchmark's value compounds only if it's fed from real near-misses, not just written once.
- **Synthetic corpus vs. real incident distribution.** Synthetic incidents are safe to commit and share but won't perfectly reflect the vocabulary/noise of the user's actual logs (typos, pasted stack traces, terse notes). A private, gitignored supplementary fixture built from real (anonymized-if-needed) incidents closes this gap for personal calibration, at the cost of not being shareable or CI-portable.
- **Fixed thresholds age.** A threshold calibrated against today's embedding model will need conscious re-baselining after a model or tokenizer upgrade — done deliberately (edit `thresholds.yaml` in the same PR as the upgrade, with the new numbers visible in review), not by silently loosening gates to make CI pass.

## Complexity & Maintenance

This is intentionally a few hundred lines of code, not an eval framework:

- `tests/retrieval/fixtures/corpus.yaml` — ~150–250 synthetic incidents (data, not code).
- `tests/retrieval/fixtures/queries.yaml` — ~40–60 labeled queries including the confusable-pair `must_outrank` cases.
- `tests/retrieval/thresholds.yaml` — per-profile (`hybrid`, `lexical_only`) gate values.
- `tests/retrieval/test_retrieval_eval.py` — loads fixtures into a scratch SQLite DB via the real ingestion path, runs both profiles through the real search function, computes metrics (via a small metrics dependency or ~50 lines of hand-rolled P@K/Recall@K/MRR/pairwise-accuracy), asserts against thresholds, and prints a per-category/per-pair table.
- `scripts/eval_retrieval.py` — thin CLI wrapper around the same harness for a human-readable report outside pytest (useful when iterating on ranking, not just gating CI).

**Runtime cost:** sub-few-seconds for `lexical_only`; the `hybrid` profile's cost is dominated by embedding the fixture corpus once, which should be cached (checked-in vectors, or a fast local cache keyed by corpus content hash) so routine test runs don't recompute embeddings or depend on network availability.

**Ongoing maintenance is mostly data, not code:** adding a new category or a newly-discovered confusable pair means appending a few YAML entries, not touching the harness. The two places that do need conscious human attention over time are (1) `thresholds.yaml` re-baselining after a deliberate model/index change, and (2) periodically (e.g. whenever a real search miss is noticed while using the tool) adding the miss as a new fixture pair so the benchmark keeps tracking real failure modes rather than staying static. Neither requires infrastructure beyond what's described above, and none of it depends on the AI/embedding stack being available to *run* the tests — only the `hybrid` profile's own pass/fail depends on that, exactly mirroring the graceful-degradation requirement the eval exists to protect.

---

## AI Provider Abstraction & Structured Extraction

Engineering Memory needs one seam between "raw incident text + evidence files" (canonical, in SQLite/disk) and "structured, queryable knowledge" (title, root_cause, solution, tags, technologies, environment, lessons — all derived and rebuildable). That seam is the `AIProvider` abstraction. It has to (a) work identically whether the backend is Claude, an OpenAI-compatible endpoint, or a local model, (b) refuse to fabricate facts and say so per field, and (c) fail into a state where lexical search still works when no AI backend is reachable at all.

### Recommendation

**Interface.** Define a small `Protocol`/ABC, not a framework:

```python
class ExtractionResult(BaseModel):
    fields: IncidentExtraction          # the canonical Pydantic schema (title, root_cause, ...)
    provenance: dict[str, FieldProvenance]  # per-field: value/basis/confidence/evidence_quote
    provider: str        # "claude" | "openai_compatible" | "local" | "heuristic"
    model: str
    prompt_version: str
    schema_version: str
    raw_response: str | None   # kept for audit / re-parsing, not the source of truth

class AIProvider(Protocol):
    def is_available(self) -> bool: ...
    def extract(self, incident_text: str, evidence_context: list[str]) -> ExtractionResult: ...
```

`IncidentExtraction` (Pydantic) is the **single source of truth** for the schema. Every provider adapter derives its wire-format schema from `IncidentExtraction.model_json_schema()` plus a small per-vendor transform, instead of hand-maintaining three parallel schemas that will drift.

**Structured-output mechanism per provider (current, Sept 2026):**

| Provider | Mechanism | Notes |
|---|---|---|
| **Claude** (`anthropic` SDK) | `messages.create(..., output_config={"format": {"type": "json_schema", "schema": {...}}})`, or the `client.messages.parse()` helper that validates automatically. The older `output_format` param is deprecated. | Default model `claude-sonnet-5` ($2/$10 per MTok) for routine extraction; escalate to `claude-opus-5` for long/ambiguous incidents. Always check `stop_reason == "refusal"` before reading `content` — safety declines return HTTP 200, not an error. When extraction runs as one step inside an MCP/Claude-Code-driven ingestion flow (this project integrates with both), the alternate path is a `strict: true` tool with `additionalProperties: false` + `required` — same schema, invoked as a tool call rather than a bare structured response. Keep both paths behind the same `ClaudeProvider`. |
| **OpenAI-compatible** | Chat Completions `response_format: {"type": "json_schema", "json_schema": {"name": ..., "schema": {...}, "strict": true}}`. | Chosen deliberately over the newer OpenAI **Responses API** (`text.format`) as the *default* wire shape, because "OpenAI-compatible" in practice means self-hosted/third-party servers (vLLM, Ollama's OpenAI-compat endpoint, Together, Groq, Fireworks) that implement Chat Completions, not Responses. Native OpenAI users get GPT-5.6 Terra ($2/$12/MTok) as the default, Luna ($0.20/$1.20) for a cheap high-volume tier. Not every "compatible" backend actually supports strict `json_schema` — some only do loose `json_object` mode or nothing. Treat this as a **runtime-probed capability**, not an assumption: probe once at provider init, and if strict mode isn't available, fall back to a JSON-mode prompt + a JSON-repair pass (`json_repair` or similar) + re-validate against the Pydantic schema before accepting the result. |
| **Local models (optional)** | Ollama's `format` parameter (JSON-schema-driven, XGrammar-backed grammar-constrained decoding) or vLLM's `guided_json`/`guided_decoding`. | Grammar-constrained decoding guarantees *syntactically* valid JSON (malformed output isn't even a candidate token), but does **not** guarantee semantic correctness — small local models are noticeably weaker at synthesizing root_cause/lessons than Claude/GPT-tier models. Treat local extraction as a lower-confidence tier by default, useful mainly for fully offline operation, not as an iso-quality peer. Since Ollama also speaks the OpenAI-compatible wire format, it can reuse the `OpenAICompatibleProvider` pointed at `localhost` rather than needing a fully separate adapter. |
| **Unavailable / degraded** | `HeuristicProvider` — no LLM call at all. | Title = first non-blank line or filename; tags = keyword-dictionary match against a small static list; root_cause/solution/lessons left null; record flagged `needs_ai_review = true`. This is what keeps SQLite FTS5 lexical search fully functional with zero AI dependency, satisfying the "must always work" requirement. |

**Prompt design — "do not invent facts, mark inferred fields."** Two rules, enforced at both the prompt and the schema level, not prompt alone:
1. The system prompt is explicit: extract only what the evidence text states; never fill in a plausible-sounding hostname, date, version, or root cause that isn't present; when a field can't be determined, either omit it or mark it explicitly rather than guessing.
2. The schema forces the model to justify itself per field: every extracted field carries a **basis** — `explicit` (a literal fact from the text), `inferred` (reasonably implied but not stated), `synthesized` (root_cause/solution/lessons are almost always this — the model is summarizing, not quoting), or `unknown`. "Hard fact" fields (technologies, environment, specific error strings, timestamps) additionally require a verbatim `evidence_quote`; narrative fields (root_cause/solution/lessons) don't, since forcing a literal quote there would just suppress legitimate synthesis.

**Provenance/confidence tracking.** Don't trust self-reported confidence alone — LLM confidence self-reports are known to be poorly calibrated. Instead:
- For every field with an `evidence_quote`, run a deterministic post-hoc check: fuzzy/substring-match the quote against the raw incident text (e.g. `rapidfuzz`). If the quote isn't actually present, downgrade confidence and flip `basis` toward `inferred` regardless of what the model claimed. This anchors trust to the canonical evidence text — exactly the "SQLite + evidence files are the source of truth" principle — rather than to the model's own word.
- For `synthesized` fields, keep the model's self-reported confidence as a secondary signal only (better than nothing, worse than verification).
- Persist provenance in its own table: `extraction_provenance(incident_id, field_name, value, basis, confidence, evidence_quote, extractor_provider, extractor_model, prompt_version, schema_version, extracted_at)`. Because it's keyed by provider/model/prompt/schema version, extractions are fully re-runnable against the (unchanged) canonical evidence whenever a prompt is improved or a model is swapped — no destructive migration, matching the "everything but SQLite+evidence must be rebuildable" constraint.

### Alternatives Considered

- **Adopt `instructor` (multi-provider, Pydantic-based) instead of hand-rolling.** It already spans Claude/OpenAI/Ollama and handles retries/validation well. Rejected as the *primary* abstraction: it doesn't have first-class per-field provenance/confidence (we'd build that layer on top regardless), and it abstracts away exactly the provider-specific mechanics we need explicit control over — Claude's refusal `stop_reason`, `output_config.format` vs. strict tool-use, per-backend capability probing for "OpenAI-compatible" servers. The dependency's own upstream churn becomes another thing to track for modest saved code. Reasonable to borrow *ideas* from it, not to depend on it wholesale.
- **Adopt `litellm` as the provider router.** Wide provider coverage (100+) via a unified call shape, but it mainly unifies request/response shape, not structured-output guarantees — the Claude/OpenAI JSON-schema divergence still has to be handled above it. With only 2-3 providers actually in scope, the coverage doesn't pay for its weight as an extra moving part to pin and upgrade.
- **Independent hand-written schema/prompt per vendor** instead of one Pydantic model. More per-vendor tuning latitude, but guarantees schema drift over time across a single-maintainer project — rejected in favor of one schema with thin per-vendor adapters.
- **Self-reported confidence only, no verification pass.** Cheapest (one call, no post-processing), but known to be poorly calibrated; rejected as the sole mechanism, kept as a secondary signal for synthesized fields only.
- **A second LLM-judge pass to verify each extraction.** More accurate at catching subtle hallucination than substring verification, but doubles cost/latency per incident and adds a second AI dependency to the critical path — more machinery than a personal-scale (10k incidents) tool justifies. Substring/fuzzy verification gets most of the benefit deterministically and for free.
- **Native OpenAI Responses API as the default OpenAI(-compatible) path.** More capable (agentic primitives, native multimodal) but OpenAI-only — most self-hosted/third-party "OpenAI-compatible" servers implement Chat Completions, not Responses. Chosen Chat Completions `json_schema` strict mode as the default for maximum compatibility; Responses API left as an optional richer path when talking to OpenAI directly.

### Tradeoffs

- Hand-rolling the abstraction means owning more test surface (need cassette/fixture-based tests per provider so CI doesn't require live keys) in exchange for full control over refusal handling, capability probing, and the provenance scheme that off-the-shelf libraries don't provide out of the box.
- Requiring a verbatim `evidence_quote` for hard-fact fields is robust against hallucination but would hurt recall if applied uniformly (root_cause is usually synthesized across several log lines, never a literal quote) — mitigated by making the quote requirement per-field-type rather than global, at the cost of a slightly more complex schema.
- Standardizing OpenAI-compatible traffic on Chat Completions' `json_schema` mode sacrifices some native-OpenAI-only capability, but is the right call for a system whose explicit goal is graceful degradation across heterogeneous (including self-hosted) backends.
- Local models guarantee syntactic JSON validity via constrained decoding but not semantic reliability — positioning local as a lower-confidence "better than nothing when offline" tier (rather than a peer to Claude/OpenAI) is a deliberate, honest constraint, not a gap to hide.
- Multiple provider adapters means multiple rate-limit/error/quirk surfaces to maintain — bounded by deliberately *not* chasing every vendor feature (batches, fast mode, etc.), only the single-shot structured-extraction primitive actually needed here.

### Complexity & Maintenance

Footprint is modest: one ABC/Protocol (~50 lines), 3–4 concrete providers (~100–150 lines each: Claude, OpenAI-compatible, optional local, heuristic fallback), one shared Pydantic schema module, and one provenance-verification module (~80 lines) — roughly 600–800 lines total, testable offline via recorded-response fixtures.

Ongoing costs to plan for:
- **Vendor API drift.** Both Anthropic and OpenAI have shipped breaking structured-output renames within the current year alone (Claude's `output_format` → `output_config.format`; OpenAI's push toward the Responses API as the recommended primitive). Pin SDK versions, isolate each vendor's wire-format quirks to its own adapter file, and add a periodic ("does our adapter still match vendor docs") check.
- **Model deprecation.** Because each extraction row records `extractor_model` + `prompt_version` + `schema_version`, old incidents can be identified and selectively re-run against a newer model without touching the rest of the corpus.
- **Local-model churn.** The Ollama/vLLM/grammar-engine ecosystem (XGrammar, Outlines, GBNF) is the fastest-moving part of this design — keeping it as an optional adapter behind the same interface means it can be swapped or dropped without touching the Claude/OpenAI paths.
- **Eval coverage.** Since this feeds a long-lived knowledge corpus, a small golden set (10–20 real incidents with human-labeled fields) run against each provider/model catches prompt or schema-adapter regressions before they reach real data — cheaper to build once than to debug silently-wrong extractions later.

### Migration Risk

- **Low, at the storage layer.** SQLite + evidence files stay canonical; extraction and provenance tables are explicitly derived/rebuildable, so any provider swap, adapter fix, or model upgrade can be replayed by re-running extraction over stored evidence text — no data loss, no destructive migration.
- **Medium, on provider API drift.** Structured-output parameter shapes have changed at least once on both Claude and OpenAI within the current year; isolating this to two small adapter files bounds the blast radius, but model IDs and beta flags need periodic review (e.g., a scheduled `GET /v1/models` check) to catch deprecations before they start returning 400s in production.
- **Medium, on the "OpenAI-compatible" abstraction leaking.** Not every self-labeled compatible backend actually implements strict `json_schema` mode — treating support as a runtime-probed capability (with repair-and-validate as the fallback) rather than an assumption keeps this from becoming a silent correctness gap; worth maintaining a short list of known-good backends vs. best-effort ones.
- **Low–medium, on the provenance scheme itself.** It's a separate, versioned table, so extending it (e.g., a new `basis` value) is additive; existing rows keep the version they were written under and aren't silently reinterpreted.
- **Rollback is cheap.** Providers are config-selected and stateless per call, so switching model or provider (Sonnet 5 → Opus 5, or Claude → local) is a config change plus an optional batch re-extraction — no schema migration needed unless the shared `IncidentExtraction` schema itself changes, in which case a lightweight Alembic migration touches only the derived tables, never the canonical evidence store.

---

## Attachment Ingestion & OCR

### Recommendation

**Concrete pipeline** for turning an uploaded attachment into searchable/embeddable text without ever blocking the save of the incident record:

```
Attachment saved (original bytes) → sniff type → route to extractor → write extracted_text row
                                                        │
                                       (any failure at any stage is caught,
                                        logged, and never propagates up to
                                        the incident-save transaction)
```

**1. Type detection** — don't trust the file extension alone. Use stdlib `mimetypes` for the fast path plus a magic-byte sniff (the `filetype` package, or `python-magic` if `libmagic` is already available on the box) as a fallback for extension-less pastes/log dumps. Classify into: `text/plain`-like (logs, code, terminal dumps, `.md`, `.json`, `.yaml`, …), `application/pdf`, `image/*` (screenshots).

**2. Plain text / logs / code / terminal output** — no extraction library needed. Read bytes, detect encoding with `charset-normalizer` (maintained successor to `chardet`, MIT), decode with `errors="replace"` as a last resort so a single bad byte never fails ingestion. Store the decoded text as-is for logs/code (preserve ANSI escape codes and control chars in the *original* file only); store a **cleaned copy** (ANSI stripped, non-printables removed) as the `extracted_text` used for FTS/embeddings, since raw escape sequences hurt lexical/semantic search quality.

**3. PDFs** — **PyMuPDF (`pymupdf`, currently 1.28.x)** as the primary extractor:
- One dependency does both text extraction (`page.get_text()`) *and* page rasterization (`page.get_pixmap()`) for the OCR-fallback path below — no need for a separate Poppler/`pdf2image` install.
- 8–12x faster than `pdfplumber` on plain text, handles malformed/real-world PDFs (scanned reports, exported Jira/Confluence pages, vendor postmortems) more robustly than `pypdf`.
- **Fallback/complement:** if a page yields near-zero text (common for a PDF that's actually a screenshot pasted into a doc, or a true scan), treat it as image content and route through the OCR path (step 4) using the page rendered at ~300 DPI via `get_pixmap(dpi=300)`.
- Keep **`pypdf` (currently 6.17.x, BSD)** as a lightweight secondary/fallback extractor for the rare PDF PyMuPDF chokes on, and as the license-safe option (see Tradeoffs — PyMuPDF is AGPL-3.0). If the project is ever distributed/open-sourced rather than run purely locally by one person, pypdf becomes the default and PyMuPDF the optional accelerator.

**4. Screenshots (terminal output, error dialogs, stack traces)** — **Tesseract 5.x via `pytesseract`**, with a small deterministic preprocessing step using Pillow (already a transitive dependency of most of this stack):
- Convert to grayscale.
- Upscale if the shorter edge is under ~1000px (Tesseract's accuracy falls off sharply below ~10pt text at 300 DPI — most terminal screenshots are well under that natively).
- Binarize (Tesseract 5 already runs adaptive Otsu/Sauvola internally, but an explicit threshold pass first measurably helps on low-contrast screenshots).
- **Invert dark-mode terminals** — detect when the image's mean luminance is low (light text on dark background, the default for most terminal themes) and invert before OCR; Tesseract is tuned for dark-text-on-light-background and degrades badly otherwise.
- Run with `--oem 1` (LSTM engine) and `--psm 6` as the default (uniform block of monospaced text); expose `--psm 11` (sparse text) as a per-attachment override for screenshots with scattered UI chrome around the actual error text.
- Capture Tesseract's mean word confidence (`image_to_data`) and store it alongside the extracted text so low-confidence OCR can be flagged/deprioritized in search ranking rather than silently trusted.

**5. Code files** — treated as plain text (step 2); no OCR/PDF logic needed, just skip binary files (detect via null-byte sniff or the `filetype`/`magic` check) and mark them `extraction_status=skipped_binary`.

---

### Alternatives Considered

| Need | Chosen | Considered | Why not chosen as default |
|---|---|---|---|
| PDF text extraction | **PyMuPDF** | `pypdf` | Pure-Python, weaker layout/column handling, garbles real-world technical PDFs more often; kept as fallback/license-safe option |
| | | `pdfplumber` | Best-in-class *table* extraction (financial/tabular docs) but 8–12x slower, character-level parsing; overkill when incidents attachments are mostly logs/screenshots-as-PDF, not tables |
| Local OCR | **Tesseract 5 (`pytesseract`)** | `EasyOCR` | Higher accuracy on some benchmarks (handwriting, mixed script) but ~500MB model download, ~3x slower, GPU-friendly but CPU-slow — heavy for a tool meant to run comfortably on a laptop for 10k incidents |
| | | `PaddleOCR` | Cited as the most accurate free OCR in several 2026 comparisons, especially with layout/table analysis, but larger install, more moving parts (PaddlePaddle framework dependency), less mature Python packaging story than Tesseract |
| | | Cloud OCR (Google Vision, AWS Textract, Claude/GPT vision) | Explicitly excluded — violates the fully-local/offline requirement and the "must degrade gracefully without AI" principle; could be offered later as an *optional* higher-quality path behind the same interface, never as the only path |
| Scanned-PDF OCR glue | Render via **PyMuPDF + pytesseract** directly | `ocrmypdf` | `ocrmypdf` is excellent but is designed to *write a searchable PDF back out*; this system wants raw extracted text into SQLite, not a rewritten PDF, so it's an unnecessary extra dependency/subprocess for this use case |

Design choice underlying all of this: one **pluggable extractor interface** (`extract(attachment) -> ExtractionResult`) per MIME class, so PaddleOCR/EasyOCR or a future local vision-LLM can be dropped in later as an alternate engine without touching ingestion, storage, or the "must not block save" guarantee.

---

### Tradeoffs

- **PyMuPDF licensing (AGPL-3.0 / Artifex commercial dual license):** using it as a dependency is unrestricted for a tool that stays on the user's own machine for their own use. It becomes relevant only if this project is later distributed as a hosted/multi-tenant service or the source is shipped in a way that triggers AGPL's copyleft — at that point either swap the default PDF extractor to `pypdf`/`pdfplumber` (MIT/BSD) or budget for the Artifex commercial license. Recommendation: keep the extractor behind an interface specifically so this swap is a config change, not a rewrite.
- **Tesseract vs. PaddleOCR/EasyOCR accuracy:** Tesseract is not the most accurate free OCR engine in 2026 benchmarks — PaddleOCR generally wins on messy/tabular/multilingual input. For clean printed monospaced terminal/error screenshots (this system's actual workload), Tesseract's accuracy-to-footprint ratio is the better fit; the gap matters more for handwriting, phone-photographed screens, or non-Latin scripts, which are edge cases here, not the common case.
- **OCR is inherently lossy and non-deterministic across versions.** A Tesseract 5.4 vs 5.5 upgrade can shift extracted text slightly. Store `extractor_engine` + `extractor_version` + a confidence score per attachment so this is visible and auditable, and so lexical search results can be explained ("this hit came from a 62%-confidence OCR pass") rather than trusted blindly.
- **PDF fallback heuristic (near-zero text → treat as image) is a heuristic, not a guarantee.** A PDF with a handful of text characters and mostly diagrams could go either way; make the "OCR this PDF page too" threshold configurable and log the decision so it's debuggable per-attachment rather than a silent black box.

---

### Portability & Offline

- **All chosen libraries run fully offline after initial install** — PyMuPDF/pypdf are pure extraction logic with no network calls; Tesseract's language data (`tessdata`) is a one-time download that must be **vendored/bundled or pinned to a local path** at setup time (not left to lazy-download-on-first-use), otherwise a fresh machine or air-gapped environment silently fails OCR on first real use.
- **External binary dependency:** Tesseract itself is a system binary (`apt`/`brew`/choco), not a pip package — `pytesseract` is just a thin wrapper that shells out to it. This must be:
  1. Documented per-OS in setup docs,
  2. Checked at application startup with a clear health-check (`pytesseract.get_tesseract_version()` in a try/except),
  3. Surfaced to the user as "OCR unavailable" rather than a crash — attachments still save, text/code/log/PDF-native-text extraction still works, and lexical search still works over whatever text *is* available, consistent with the system's "must degrade gracefully" requirement.
- **No Poppler dependency needed** — routing scanned-PDF pages through PyMuPDF's own rasterizer instead of `pdf2image` removes one more external-binary install target and one more thing that differs across OS/machine.
- Everything (PyMuPDF, pypdf, pdfplumber, pytesseract, Pillow, charset-normalizer) has wheels for Linux/macOS/Windows on current Python (3.11–3.13 range as of 2026), so no compiler toolchain is required for a normal `pip install`.

---

### Complexity & Maintenance

- **Isolate extraction as its own service-layer module** with one interface per MIME class and a strict contract: it always returns a result object (`status: success | partial | failed | skipped`, `text`, `confidence`, `engine`, `engine_version`, `error`), it never raises past its own boundary, and it never touches the original file (read-only).
- **Never let extraction be synchronous-blocking on the request that saves an incident/attachment.** Even at this scale (10k+ incidents, not millions), OCR on a large or pathological image can occasionally hang — call Tesseract through `pytesseract`'s `timeout=` parameter (it kills the subprocess) and treat a timeout as `status: failed`, not a crash.
- **Keep a reprocessing path** (a CLI command or admin endpoint) that re-runs extraction for rows marked `failed` or whose `engine_version` is older than current — this is the safety net for "Tesseract wasn't installed when this was first saved" or "we upgraded PyMuPDF and want better text now."
- Maintenance burden is concentrated in exactly one place: the Tesseract system-binary install/version story. The pip-only libraries (PyMuPDF, pypdf, Pillow, charset-normalizer) update like any other dependency with no special handling.

---

### Migration Risk

- **Extracted text is a rebuildable cache, not canonical data** — consistent with the project's core principle. The `extracted_text` row in SQLite is *derived* from the original attachment bytes (the true source of truth) and is safe to wipe/regenerate at any time; nothing is lost by dropping and re-extracting, only recomputed (and possibly slightly different, per the non-determinism note above). This should be reflected directly in the schema: every extracted-text row carries `source_attachment_hash`, `extractor_engine`, `extractor_version`, and `extracted_at`, so staleness is detectable and reprocessing is scoped rather than a blind full rebuild.
- **Library upgrades can silently change output.** Pin exact versions in `requirements.txt`/lockfile (e.g., `pymupdf==1.28.2`, `pypdf==6.17.0`) rather than floating ranges, precisely because this system's rebuildability guarantee assumes "rebuild" means "rerun the same deterministic process" — an unpinned upgrade mid-project changes what "rebuild" produces without anyone deciding that on purpose.
- **Machine migration (new laptop, new OS) risk is concentrated in the Tesseract system binary and its language data**, not in the Python packages. A `pip install -r requirements.txt` alone will silently leave OCR non-functional on a new machine unless the setup docs/health-check catch it. Recommend a first-run diagnostic that checks for the Tesseract binary and the expected `tessdata` language files and reports exactly what's missing.
- **AGPL exposure is a migration/distribution risk, not a runtime risk** — it only manifests if/when the project moves from "runs locally for one person" to "distributed to others" (open-sourcing the repo as a runnable service others self-host is fine either way; operating it as a hosted multi-tenant service is where PyMuPDF's AGPL terms would need to be revisited). Flagging it now, before it's load-bearing, is cheaper than discovering it after the extractor interface has hard-coded PyMuPDF-specific APIs.
- **OCR confidence drift across re-extractions is invisible unless recorded.** Without storing per-attachment `extractor_version` + confidence, a future re-extraction pass has no way to tell "this text got worse" from "this text got better" — store both so any future migration/reprocessing job can diff and report rather than overwrite blind.

Sources:
- [pymupdf · PyPI](https://pypi.org/project/pymupdf/)
- [Python PDF library comparison (2026): 7 libraries for developers](https://www.nutrient.io/blog/best-python-pdf-libraries/)
- [PyMuPDF vs pdfplumber (2026): the speed-vs-license tradeoff, benchmarked — pdfmux](https://pdfmux.com/blog/pymupdf-vs-pdfplumber/)
- [pdfplumber vs PyMuPDF vs pypdf (PyPDF2): Which to Use](https://subhajitbhar.com/blog/pdf-extraction/pdfplumber-vs-pymupdf-vs-pypdf2/)
- [Installation — pypdf 6.16.0 documentation](https://pypdf.readthedocs.io/en/stable/user/installation.html)
- [Python OCR with pytesseract: Extract text from images using Tesseract (2026)](https://www.nutrient.io/blog/how-to-use-tesseract-ocr-in-python/)
- [Best Python OCR Library for Invoices: 6 Engines Compared (2026)](https://invoicedataextraction.com/blog/python-ocr-library-comparison-invoices)
- [PaddleOCR vs Tesseract vs EasyOCR: OCR Speed and Accuracy 2026 | CodeSOTA](https://www.codesota.com/ocr/paddleocr-vs-tesseract)
- [Tesseract PSM and OEM modes: Configuration and tuning in Python](https://www.nutrient.io/blog/tesseract-python-guide/)
- [Improving OCR Quality | tesseract-ocr/tessdoc | DeepWiki](https://deepwiki.com/tesseract-ocr/tessdoc/6-improving-ocr-quality)
- [Python PDF text extraction with PyMuPDF: Scanned PDFs, tables, and OCR](https://www.nutrient.io/blog/extract-text-from-pdf-pymupdf/)
- [Python OCR libraries for converting PDFs into editable text](https://ploomber.io/blog/pdf-ocr/)

---

## Knowledge Graph

Engineering Memory's knowledge graph has four relationship shapes: **Incident↔Technology** and **Incident↔Project** (bipartite, largely untyped or lightly-typed many-to-many), and **Incident↔Incident** (`related_to` / `caused_by` / `solved_by` / `supersedes`, directed and typed) and **Technology↔Technology** (`depends_on` / `part_of` / `replaces`, self-referential). The two query patterns that matter in the UI are (1) **N-hop traversal** — "show incidents related to this one, out to 2–3 hops" — and (2) **relationship-type filtering** — "only `caused_by` edges" or "only edges sourced by a human, not AI-suggested." Everything below assumes the stated scale envelope: ~10,000 incidents, modest edge density (single-digit-to-low-tens of edges per node, so on the order of 10⁴–10⁵ edges total, not 10⁶+).

### Recommendation

**Model the graph as relational SQLite tables. Do not introduce a dedicated graph database.** Concretely:

- Keep the two bipartite relationships as ordinary many-to-many join tables (`incident_technologies`, `incident_projects`).
- Model each self-referential relationship type-family as its own **self-referencing edge table** (`incident_relations`, `technology_relations`) with an explicit `relation_type` column, rather than a single generic polymorphic `edges` table spanning multiple node types.
- Implement "related incidents" traversal with SQLite's `WITH RECURSIVE` CTEs, with a depth cap (2–3 hops is the practically useful range for a human browsing an incident) and a cycle guard.

```sql
-- Bipartite many-to-many
CREATE TABLE incident_technologies (
  incident_id   INTEGER NOT NULL REFERENCES incidents(id)   ON DELETE CASCADE,
  technology_id INTEGER NOT NULL REFERENCES technologies(id) ON DELETE CASCADE,
  relevance     TEXT,                          -- e.g. 'root_cause' | 'affected' | 'mentioned'
  source        TEXT NOT NULL DEFAULT 'human',  -- 'human' | 'ai_extracted'
  confidence    REAL,                           -- only meaningful when source = 'ai_extracted'
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (incident_id, technology_id)
);
CREATE INDEX idx_incident_technologies_tech ON incident_technologies(technology_id);

CREATE TABLE incident_projects (
  incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  project_id  INTEGER NOT NULL REFERENCES projects(id)  ON DELETE CASCADE,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (incident_id, project_id)
);
CREATE INDEX idx_incident_projects_project ON incident_projects(project_id);

-- Self-referential, typed: Incident <-> Incident
CREATE TABLE incident_relations (
  id                  INTEGER PRIMARY KEY,
  source_incident_id  INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  target_incident_id  INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  relation_type       TEXT NOT NULL CHECK (relation_type IN
                        ('related_to','caused_by','solved_by','supersedes','duplicate_of')),
  is_symmetric        INTEGER NOT NULL DEFAULT 0,  -- 1 for related_to/duplicate_of
  source              TEXT NOT NULL DEFAULT 'human', -- 'human' | 'ai_suggested'
  confidence          REAL,
  note                TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (source_incident_id, target_incident_id, relation_type),
  CHECK (source_incident_id != target_incident_id)
);
CREATE INDEX idx_incident_relations_src ON incident_relations(source_incident_id, relation_type);
CREATE INDEX idx_incident_relations_tgt ON incident_relations(target_incident_id, relation_type);

-- Self-referential, typed: Technology <-> Technology
CREATE TABLE technology_relations (
  id                    INTEGER PRIMARY KEY,
  source_technology_id  INTEGER NOT NULL REFERENCES technologies(id) ON DELETE CASCADE,
  target_technology_id  INTEGER NOT NULL REFERENCES technologies(id) ON DELETE CASCADE,
  relation_type         TEXT NOT NULL CHECK (relation_type IN
                          ('depends_on','part_of','replaces','related_to')),
  created_at            TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (source_technology_id, target_technology_id, relation_type),
  CHECK (source_technology_id != target_technology_id)
);
CREATE INDEX idx_technology_relations_src ON technology_relations(source_technology_id, relation_type);
CREATE INDEX idx_technology_relations_tgt ON technology_relations(target_technology_id, relation_type);
```

**Design notes baked into this schema:**

- **Directionality is a documented convention, not a schema decision** — decide once (e.g. "`A caused_by B` means B is the cause of A") and write it in the model docstring before any row is written. Getting this wrong and needing to flip existing rows later is the single most annoying migration this design can incur.
- **Symmetric types get a canonical row, not two.** For `related_to`/`duplicate_of`, always insert with `source_incident_id < target_incident_id` (enforce with a `CHECK` or in the service layer) and match both directions at query time with an `OR`. This avoids duplicate/conflicting rows and keeps the unique index meaningful.
- `relation_type` as a `CHECK` constraint is fine while the vocabulary is small and stable (as here). If the team expects to add relation types frequently without a migration, swap it for a `relation_types(code PK, label, is_symmetric)` lookup table + FK — flagged as an option, not the default, since SQLite CHECK-constraint changes are cheap at this scale anyway (see Migration Risk).
- `source`/`confidence` columns exist so AI-suggested edges (from embedding similarity or LLM extraction) can live in the same table as human-curated ones, filterable and distinguishable — but the table itself has **zero dependency on the AI subsystem being available**: it's plain relational data, so relationship browsing keeps working lexically if embeddings/vector index are down, consistent with the graceful-degradation requirement.

**N-hop traversal ("related incidents"), depth- and cycle-bounded, optionally type-filtered:**

```sql
WITH RECURSIVE frontier(incident_id, depth, path) AS (
  SELECT :start_id, 0, ',' || :start_id || ','
  UNION
  SELECT
    CASE WHEN r.source_incident_id = f.incident_id
         THEN r.target_incident_id ELSE r.source_incident_id END,
    f.depth + 1,
    f.path || (CASE WHEN r.source_incident_id = f.incident_id
                    THEN r.target_incident_id ELSE r.source_incident_id END) || ','
  FROM incident_relations r
  JOIN frontier f
    ON r.source_incident_id = f.incident_id OR r.target_incident_id = f.incident_id
  WHERE f.depth < :max_depth
    AND instr(f.path, ',' || (CASE WHEN r.source_incident_id = f.incident_id
                                   THEN r.target_incident_id ELSE r.source_incident_id END) || ',') = 0
    AND (:relation_type IS NULL OR r.relation_type = :relation_type)
)
SELECT incident_id, MIN(depth) AS depth
FROM frontier
WHERE incident_id != :start_id
GROUP BY incident_id
ORDER BY depth;
```

This is the standard SQLite graph-traversal pattern (depth counter + accumulated `path` string as a cycle guard via `instr`). With the two indexes above, at 10k incidents / tens of thousands of edges this runs in single-digit-to-low-tens of milliseconds — comfortably inside interactive UI latency, and reusable verbatim (swap table/column names) for `technology_relations`. SQLAlchemy 2.0's Core `select(...).cte(recursive=True)` renders `WITH RECURSIVE` directly, so this doesn't need to be raw SQL in the app — it composes with the existing ORM models. As of writing, SQLite's current stable release is 3.53.4 (July 2026) and SQLAlchemy's is 2.0.52 (Aug 2026, with 2.1 in RC) — recursive CTE support has been stable in both for years, so there's no version risk here. ([SQLite release notes](https://sqlite.org/changes.html), [SQLAlchemy releases](https://github.com/sqlalchemy/sqlalchemy/releases), [SQLAlchemy self-referential docs](https://docs.sqlalchemy.org/en/20/orm/self_referential.html))

Relationship-type filtering is just a `WHERE relation_type = ?` against the composite indexes above — no traversal engine involved.

### Alternatives Considered

1. **Dedicated graph database (Neo4j, Memgraph, etc.), run as a sidecar service.** Rejected: introduces a second database engine with its own process/config/auth, a second backup/restore story, a query language (Cypher) the FastAPI backend and MCP tools would both need to learn to generate safely, and a second thing that can be "unavailable" requiring its own graceful-degradation path — on top of the AI/embeddings/vector-index degradation already required. None of this buys traversal performance the app needs at 10k incidents.

2. **Embedded graph database (Kùzu) in-process, no server.** More attractive on paper — no network hop, Cypher support, columnar storage tuned for multi-hop analytical traversal. Rejected for this project: it's a second storage engine and second schema/migration story alongside Alembic-managed SQLite, and — concretely — **Kùzu was acquired by Apple in October 2025; its open-source repo has been archived and the project site taken down**, with existing releases usable but no further development planned. Betting a "must be rebuildable, local-first, long-lived" system's core relationship data on an archived embedded engine is a real risk, independent of whether it would have been fast enough. ([Kùzu status via The Data Quarry](https://thedataquarry.com/blog/embedded-db-2/), [PuppyGraph on Kùzu](https://www.puppygraph.com/blog/what-is-kuzudb))

3. **Generic polymorphic edge table** (`nodes(id, type)` + `edges(src_node_id, dst_node_id, relation_type, metadata_json)`) spanning all node types. Rejected in favor of per-type-pair self-referencing tables: the polymorphic form gives up FK integrity at the database layer (a `nodes` indirection table is needed for FKs to work at all across types), pushes type-correctness checks into application code, and buys "add a new relation type without a migration" flexibility that isn't needed here — the relationship vocabulary (`caused_by`, `solved_by`, `supersedes`, `depends_on`, …) is small, domain-specific, and does not change often. Typed tables also map more directly onto SQLAlchemy models and Pydantic schemas the rest of the codebase already uses.

4. **In-memory graph library (NetworkX) rehydrated from SQLite on demand for traversal/analytics.** Not rejected outright — this is a reasonable *addition*, not a replacement: for anything beyond simple bounded traversal (e.g. future graph analytics like community detection across incidents), load `incident_relations` into a NetworkX graph in a background job and treat it as a rebuildable derived index, exactly like embeddings/vector index/search index are already treated. This preserves "SQLite + evidence files are canonical, everything else rebuildable" and defers real complexity until (if ever) it's actually needed.

Independent practitioner writeups from 2026 reach the same conclusion for adjacent-scale systems, e.g. one team's account of dropping Neo4j in favor of SQLite recursive CTEs plus semantic search for a similar "personal knowledge" style app. ([SQLite as a Graph Database, DEV Community, 2026](https://dev.to/rohansx/sqlite-as-a-graph-database-recursive-ctes-semantic-search-and-why-we-ditched-neo4j-1ai))

### Tradeoffs

| | Relational SQLite (chosen) | Dedicated / embedded graph DB |
|---|---|---|
| Traversal ergonomics | Recursive CTEs work but are verbose for complex variable-length, multi-type-filtered path queries | Cypher/Gremlin expresses these more compactly |
| Graph algorithms (shortest path w/ weights, centrality, community detection) | None built-in; would need app-level NetworkX pass | Native |
| Operational surface | One engine, one file, one Alembic history, one backup | Second engine/process (or embedded lib) to run, back up, and keep schema-in-sync |
| Integrity | FK constraints enforce valid node references at the DB layer | Typically enforced in application code, not the store |
| Fits "rebuildable" architecture | Relationships live in the same canonical SQLite file as incidents — not a derived index at all | Would put canonical data (human-curated relationships) in a second store, complicating the single-source-of-truth story |
| MCP/Claude Code integration | Same SQL surface Claude Code already needs for lexical search; no new query language to generate safely | Extra query-language surface, extra failure mode to degrade gracefully around |
| Scale fit | Comfortably handles 10⁴–10⁵ edges with plain indexes | Designed for and pays off at 10⁶+ edges or deep unbounded traversal |
| New-relationship-type extensibility | Requires a migration (cheap; see below) | Schema-free by default |

The core tradeoff: a graph DB buys traversal expressiveness and native graph algorithms; at this project's scale (10k incidents, shallow 2–3-hop traversal being the only UI need), that expressiveness isn't worth a second storage engine that works against the project's explicit local-first, single-source-of-truth, graceful-degradation design goals.

### Complexity & Maintenance

- **No new dependency, driver, or connection pool.** Edge tables are SQLAlchemy models like any other; Alembic already versions the whole schema in one history.
- **Testing** uses the same pytest fixtures/factories as the rest of the schema — no separate graph DB test container to spin up or tear down.
- **MCP tools** ("find related incidents", "find incidents that share a technology") become parameterized SQL/SQLAlchemy Core queries the MCP server already knows how to run — no separate query-language exposure, and a human can drop into the `sqlite3` CLI against the same file for ad hoc exploration.
- **The recursive CTE traversal pattern is written once** (as a small SQLAlchemy helper parameterized by start node, table, max depth, and relation-type filter) and reused identically for `incident_relations` and `technology_relations`.
- **Relationships are canonical data, not a derived index** — unlike embeddings/search index/AI summaries, `incident_relations` etc. don't need a "rebuild" step at all; they live in the same backup file as everything else, which is a genuine simplification versus any design that would put human-curated relationship data in a second store.

### Migration Risk

- **Relational → graph DB later, if ever justified:** low risk. The edge tables already are an edge list (source, target, type, metadata) — exactly the shape a bulk import into Neo4j/Kùzu-successor/etc. expects. A one-off export script covers it; no lossy transformation needed.
- **Graph DB → relational (reverse):** higher friction — would require exporting the graph engine's edges back into tables anyway, plus reconciling transactional consistency between two engines in the meantime (e.g., an incident deleted in SQLite whose edges still live in the graph store). This asymmetry is itself an argument for starting relational: it's the lower-regret direction.
- **Adding a new `relation_type` value:** SQLite `CHECK` constraints require a table rebuild to alter, which Alembic handles via `batch_alter_table` — a cheap, well-trodden operation at 10k–100k row scale, but worth planning for from the first migration rather than discovering it later. If the relationship vocabulary is expected to grow often, prefer the `relation_types` lookup-table variant from day one instead.
- **Symmetric-edge storage convention** (canonical `source_id < target_id` ordering vs. duplicate reverse rows) is hard to retrofit once data has accumulated inconsistently — enforce the convention via `CHECK`/service-layer validation before any `related_to` rows are written, not after.
- **Directionality semantics** for asymmetric types (`caused_by`, `solved_by`, `supersedes`) must be fixed and documented before data entry; changing the convention later means flipping every existing row, which is avoidable entirely by writing the convention down up front.
- **No AI/embedding coupling risk:** the graph tables are populated via normal CRUD (with optional `source='ai_suggested'`/`confidence` metadata when a suggestion comes from embeddings or an LLM), so the knowledge-graph feature has no hard dependency on the AI subsystem and degrades to "still fully functional, minus AI-suggested edges" if that subsystem is unavailable — consistent with the project's lexical-search-always-works requirement.

**Sources:**
- [SQLite Release History](https://sqlite.org/changes.html) — current stable 3.53.4 (July 2026)
- [SQLAlchemy Releases (GitHub)](https://github.com/sqlalchemy/sqlalchemy/releases) — current stable 2.0.52 (Aug 2026), 2.1.0rc1 in beta
- [SQLAlchemy 2.0 Docs — Adjacency List / Self-Referential Relationships](https://docs.sqlalchemy.org/en/20/orm/self_referential.html)
- [Embedded databases: Kùzu — The Data Quarry](https://thedataquarry.com/blog/embedded-db-2/) and [PuppyGraph: What Is KùzuDB?](https://www.puppygraph.com/blog/what-is-kuzudb) — Kùzu acquired by Apple Oct 2025, repo archived
- [SQLite as a Graph Database: Recursive CTEs, Semantic Search, and Why We Ditched Neo4j — DEV Community (2026)](https://dev.to/rohansx/sqlite-as-a-graph-database-recursive-ctes-semantic-search-and-why-we-ditched-neo4j-1ai)

---

## MCP Integration

**Key facts as of 2026-09-06** (verified against modelcontextprotocol.io and the official SDK repo/docs, not from training memory):

- **Current spec revision: `2026-07-28`** (supersedes `2025-11-25`). The base protocol is now explicitly **stateless**: the `initialize`/`initialized` session handshake is no longer the foundation — every request is self-describing, carrying protocol version and capabilities in `_meta.io.modelcontextprotocol/*` fields. Servers can no longer send JSON-RPC requests to clients mid-flight; server-initiated interactions (elicitation, sampling) are now modeled as a **Multi-Round-Trip Request (MRTR)** pattern — a tool call can return `resultType: "input_required"` with an `elicitation/create` request, and the client retries the same tool call with `inputResponses` + an opaque `requestState` token.
- **Two standard transports, unchanged in shape**: `stdio` (newline-delimited JSON-RPC over a client-launched subprocess's stdin/stdout, logs to stderr) and **Streamable HTTP** (single endpoint, POST for requests, optional per-request SSE stream, optional GET for server notifications). The old two-endpoint HTTP+SSE transport from the original 2024-11-05 spec has been gone since 2025-03-26; don't build against it.
- **Official Python SDK**: package `mcp` (github.com/modelcontextprotocol/python-sdk). It is on a **v2 line** that is a deliberate breaking rewrite aligned to the 2026-07-28 spec while staying wire-compatible with older (2025-vintage) clients like current Claude Code installs. The headline break: **`FastMCP` the class was renamed `MCPServer`** (`mcp.server.fastmcp.FastMCP` → `mcp.server.mcpserver.MCPServer`), transport moved from the constructor to `.run(transport=...)`, and Python-side fields became snake_case (`tool.input_schema`, `result.is_error`). A `1.29.x` maintenance line still exists for anyone not ready to move. Exact patch numbers I found in the wild (`2.0.0` GA ~ Jul 28 2026, `2.1.1` latest per PyPI) came through general web search rather than a source I could fully corroborate twice — treat the *major-version rename* as solid, the *exact patch* as something to re-check against `pip index versions mcp` at implementation time.
- **Standalone `fastmcp`** (jlowin/PrefectHQ, `pip install fastmcp`) is a separate, faster-moving framework that keeps its *own* `FastMCP` class (it has diverged from the official SDK's renamed `MCPServer`) and adds server composition/proxying, OpenAPI-to-MCP generation, and client-side sampling helpers the official SDK doesn't have.
- **Tool annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, `title`) are part of the tool definition and are the standard vocabulary hosts use to decide when to show a consent prompt — but the spec is explicit that **clients MUST treat annotations as untrusted unless from a trusted server**; they are a UX signal, not an enforcement mechanism.
- **Structured output**: a tool can declare `outputSchema` and return `structuredContent` (any JSON value) alongside the legacy serialized-JSON `TextContent` block (kept for backward compatibility). In the Python SDK, returning a Pydantic `BaseModel`, `TypedDict`, or `dataclass` from a `@mcp.tool()`-decorated function auto-populates both the schema and `structuredContent` — no manual JSON Schema needed for the common case.

### Recommendation

Build Recall's MCP server on the **official `mcp` Python SDK, v2 line**, using its high-level decorator API (the class formerly called `FastMCP`, now `MCPServer`), served over **`stdio` only**, launched by Claude Code as a subprocess:

- Register it the way Claude Code expects: `claude mcp add --transport stdio recall -- python -m recall.mcp_server` (project or user scope, written into `.mcp.json`), wrapped in a repo setup script rather than hand-documented steps, since this is Claude-Code-surface config, not MCP protocol.
- Expose a small, explicit tool set mirroring the domain: `search_incidents`, `get_incident`, `list_incidents`, `create_incident`, `update_incident`, `delete_incident`, `link_incidents`, `list_projects`/`list_technologies`/`list_tags`. Every read tool gets `readOnlyHint: true, openWorldHint: false`; every write tool gets accurate `destructiveHint`/`idempotentHint` plus a Pydantic input model and a Pydantic/TypedDict output model, so `inputSchema`/`outputSchema`/`structuredContent` are all auto-derived rather than hand-maintained.
- Reuse the **same Pydantic schemas the FastAPI layer already returns** for MCP `structuredContent` — one service layer under both the REST API and the MCP tools, so there is exactly one implementation of "search" or "create incident," never a second copy of business logic behind the MCP server.
- Use `ctx.elicit(schema=...)` (the SDK's binding to the new MRTR/`elicitation` mechanism) for two cases: (1) `create_incident` called with an incomplete draft, to ask for missing required fields instead of guessing, and (2) a confirmation round-trip in front of `delete_incident`/destructive merges, as defense-in-depth beyond whatever confirmation the host (Claude Code) already shows.
- Design `search_incidents` to internally degrade — FTS5-only when the vector index/embeddings are unavailable — and report which retrieval modes ran (e.g. in `structuredContent`), rather than changing the tool's shape or failing when AI components are down. This keeps the "lexical search must always work" requirement enforced at the one place an external client actually calls in.

### Alternatives Considered

1. **Standalone `fastmcp` (jlowin/PrefectHQ)** instead of the official SDK — more convenience features (proxying, composition, OpenAPI generation) but a third-party dependency that has already diverged from the official SDK's naming once, and none of its extra features (multi-server composition, REST→MCP generation) are needed for one fixed, small local tool set. Rejected in favor of the officially maintained package, which better matches the project's stated bias toward long-term rebuildability over convenience.
2. **Streamable HTTP transport** instead of stdio — would allow a phone/browser client to talk to the same server, but pulls in an entire OAuth/consent/audience-validation surface (confused-deputy prevention, token-passthrough rejection, SSRF-safe metadata discovery — see Security Best Practices below) that a single-user local process doesn't need at all. Rejected for now; keep as an *additive* future transport, not a replacement, if Recall ever grows a networked/multi-device mode.
3. **MCP Resources instead of Tools** for incident data — resources fit read-only "fetch a stable URI" access (e.g., an evidence attachment) well, but can't do search/filter/create. Recommendation: use resources (via `resource_link` results returned from `get_incident`) for evidence attachments specifically, and tools for everything actionable — not one exclusively.
4. **Low-level `Server` API** (hand-written JSON-RPC handlers) instead of the high-level decorator API — full control, but means writing `inputSchema`/`outputSchema`/structured-content population by hand, duplicating validation the Pydantic models already do. Rejected as unnecessary complexity for this tool count.

### Tradeoffs

- stdio is the simplest and lowest-risk choice, but is single-client by construction (one Claude Code process holds the server open at a time) — a non-issue for a personal local tool, a real constraint if concurrent access is ever wanted.
- Auto-derived schemas from Python type hints are fast to write but make tool-calling quality a direct function of how precisely those hints are written — worth deliberately using `Literal`/`Enum` for constrained fields (e.g. incident status) and `Annotated[..., Field(description=...)]` for LLM-facing parameter docs, rather than leaving loose `str`/`dict` types.
- Structured output is additive and backward-compatible (the text block is always still returned), so adopting it broadly is low-risk.
- Committing to the v2 SDK line now avoids a second migration later, but v2 is recent enough that pinning an exact version (not a floating `>=2`) and re-testing on every bump is worth the small extra ceremony.
- Server-side `ctx.elicit()` confirmation for destructive tools is one more round trip and a bit more code per destructive tool, but is warranted specifically because the spec itself warns that `destructiveHint` is advisory only — the protocol does not guarantee any host actually prompts the user before calling a tool.

### Complexity & Maintenance

Low relative to the rest of the system: roughly 8–12 tools, one Python module, no network stack, no auth stack, no session management (the protocol itself is stateless now). Because tool schemas are the same Pydantic models the FastAPI layer already uses, adding a field to `IncidentDetail` propagates to both the REST response and MCP `structuredContent` automatically — there is no parallel schema to keep in sync. The realistic ongoing maintenance is (a) tracking SDK/spec version bumps and (b) keeping tool descriptions/annotations honest as tools are added — a short checklist per new tool (Pydantic input model, Pydantic/TypedDict output model, explicit annotations, one-line description) keeps this cheap. Ship server registration as an idempotent script/CLI command rather than manual `.mcp.json` edits, since Claude Code's own config surface (not the MCP protocol) is the part most likely to shift between Claude Code releases.

### Migration Risk

- **Highest risk**: the official SDK's v2 line is a genuine breaking rewrite (`FastMCP`→`MCPServer`, module path changes, snake_case renames, transport moved to `.run()`). Pin an exact version range in `pyproject.toml` and re-validate on every bump rather than floating.
- **Spec-level risk**: the 2026-07-28 revision's move to full statelessness and the MRTR pattern (replacing the old server→client mid-request callback model) means a lot of MCP sample code still circulating from 2024–2025 targets the old session-handshake model. Prefer the SDK's own bundled examples/CHANGELOG over generic "current-looking" tutorials found via search.
- **Low risk**: stdio's wire format (newline-delimited JSON-RPC, client-launched subprocess) has been stable since MCP's first release and remains one of exactly two standard bindings in the current spec — the single most stable thing to build against here.
- **Medium risk, separate from MCP itself**: Claude Code's `.mcp.json`/`claude mcp add` configuration surface has already changed shape at least once; isolate it behind Recall's own setup script.
- **Deferred risk**: if a networked/multi-client mode is ever added via Streamable HTTP, budget real time for the auth model — the official Security Best Practices document's confused-deputy, token-passthrough, OAuth mix-up, and SSRF sections are all specific to that transport and none of that work transfers from (or is needed by) the stdio-only design recommended here.

**Security best practices most relevant to Recall's read/write tools** (from the official 2026-07-28 Security Best Practices tutorial): use `stdio` specifically *because* it "limit[s] access to just the MCP client" with no network listener at all; if any HTTP transport is ever added, it must require an authorization token or use an access-restricted IPC mechanism, never a bare open port; servers **MUST** validate all tool inputs, implement access controls, rate-limit invocations, and sanitize outputs regardless of being single-user; never treat a returned handle/id as authentication if any cross-call state handle is ever introduced; and hosts **SHOULD** always keep a human in the loop for tool invocations — which is why the recommendation above adds server-side `elicit()` confirmation for destructive tools as defense-in-depth on top of whatever the host does.

Sources:
- [Specification — Model Context Protocol (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/)
- [Transports Overview — Model Context Protocol (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [Tools — Model Context Protocol (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
- [Security Best Practices — Model Context Protocol (2026-07-28)](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
- [MCP Python SDK v2 beta: what is new and how to try it — Pydantic](https://pydantic.dev/articles/mcp-python-sdk-v2-beta)
- [modelcontextprotocol/python-sdk — GitHub](https://github.com/modelcontextprotocol/python-sdk)
- [mcp · PyPI](https://pypi.org/project/mcp/)
- [fastmcp · PyPI](https://pypi.org/project/fastmcp/)
- [FastMCP — Tools documentation](https://gofastmcp.com/servers/tools)
- [Install and Configure MCP Servers in Claude Code (2026) — systemprompt.io](https://systemprompt.io/guides/claude-code-mcp-servers-extensions)

---

## Claude Code Integration

*Research current as of 2026-09-06. Primary sources: Claude Code's official docs at `code.claude.com/docs` (the `/docs/en/mcp-quickstart` and `/docs/en/mcp` pages) and Anthropic's engineering post on tool design. Where I could not verify a claim directly against primary docs, I've flagged it explicitly.*

### Recommendation

Ship the Engineering Memory MCP server as a **local stdio process** (a Python entry point living in this repo, reusing the same SQLAlchemy session/models as the FastAPI app so there is exactly one code path to the SQLite store), and register it with the Claude Code CLI as follows:

- **Scope: `user`**, not `project` or `local`. The whole point of Engineering Memory is to be queryable from *any* project you're debugging in — a Claude Code session in an unrelated repo should still be able to ask "have I hit this error before?" `user` scope is the only one that makes the server available across all your projects, and it stays private to you (stored in `~/.claude.json`, never committed). Keep the server's own source code and CLI entry point inside this repo; only the *registration* is global.
- **Transport: `stdio`**, not HTTP/SSE. Nothing here needs to run remotely — it needs direct, fast access to a local SQLite file and local evidence files. Stdio is also the only transport where Claude Code passes `CLAUDE_PROJECT_DIR` into the server's environment, which is convenient if you ever want project-relative evidence linking. Note SSE is explicitly called out in current docs as a deprecated transport — don't build against it.
- **A handful of consolidated, prefixed tools**, not one tool per filter/field. Anthropic's own tool-design guidance is explicit that "when tools overlap in function or have a vague purpose, agents can get confused about which ones to use," and recommends "a few thoughtful tools targeting specific high-impact workflows" plus consolidating multiple underlying operations into one tool (their example: folding `list_users`/`list_events`/`create_event` into a single `schedule_event`). For Engineering Memory that suggests something like `incident_search` (lexical + optional semantic, with filters as parameters, not separate tools), `incident_get`, `incident_log`, `incident_list_recent`, `incident_link_evidence` — all sharing the `incident_` prefix. The same guidance recommends **`user_id`-style unambiguous parameter names** over generic ones (`incident_id`, not `id`), and says to make implicit domain context explicit in the description (your evidence-file format, what "resolved" vs "open" means, date conventions, etc.) since the model won't infer it.
- **Don't lean on the MCP `instructions` field for steering.** The spec (2026-07-28) does define an `instructions` string returned at initialize time as a hint that a client "MAY" fold into its system prompt, and some current write-ups describe Claude Code's system prompt as having an "MCP Server Instructions" section. But there is at least one open report (`anthropics/claude-code` issue #43749) that Claude Desktop stores this field and never actually reads it. **I'm not fully certain whether the Claude Code CLI reliably consumes it today** — treat it as free-if-it-works, not load-bearing. Put anything Claude *must* know into individual tool descriptions instead, which is the channel every current source agrees is authoritative.
- **Use CLAUDE.md/user-rules only as a soft nudge**, not as enforcement — see Tradeoffs.

### Setup Steps

1. **Build the server** as a normal Python package entry point in this repo, e.g. `engineering_memory/mcp_server.py`, using the official MCP Python SDK, importing the existing SQLAlchemy session factory so search/read logic isn't duplicated. Expose it as a console script (e.g. `pyproject.toml` → `[project.scripts] engineering-memory-mcp = "engineering_memory.mcp_server:main"`) so it has a stable command once installed (`uv tool install .` or `pipx install -e .`).
2. **Register it at user scope**, from a terminal (not inside a `claude` session):
   ```bash
   claude mcp add --scope user engineering-memory -- engineering-memory-mcp
   ```
   or, during development before it's installed as a script:
   ```bash
   claude mcp add --scope user engineering-memory -- uv run --project /path/to/recall python -m engineering_memory.mcp_server
   ```
   This writes the entry to the top-level `mcpServers` key in `~/.claude.json` (on Windows, `%USERPROFILE%\.claude.json`).
3. **Verify the connection**: `claude mcp list` (from the shell) should show `✔ Connected`; `claude mcp get engineering-memory` shows the resolved command and scope. Inside a session, `/mcp` lets you inspect the live tool list and reconnect/re-auth without restarting.
4. **Restart is required after any config edit.** Claude Code reads `.mcp.json`/`~/.claude.json` at session start only — editing the file (or re-running `claude mcp add`) does not affect an already-running session; exit and restart, or use `/mcp` to disconnect/reconnect just that server.
5. **Add a short steering hint** in your personal, all-projects memory file `~/.claude/CLAUDE.md` (loaded into every session regardless of project), something like:
   > When the user is debugging an error, asks "have I seen this before," or wants to record a postmortem, check the `engineering-memory` MCP tools before searching the web.
   Keep it to 1–3 lines — CLAUDE.md guidance explicitly recommends concise, specific instructions and warns that longer files reduce adherence.
6. **If you ever want it repo-shared instead of personal** (e.g. teammates on the same machine/account, or you want the registration itself versioned), re-add at `--scope project`, which writes `.mcp.json` at the repo root and requires a one-time approval prompt per clone — but this is not the recommended default here since Engineering Memory is explicitly a *personal* tool meant to follow you across all your projects, not just this one.

### Tradeoffs

| Choice | Why | Cost |
|---|---|---|
| `user` scope vs `project` scope | Cross-project recall is the whole point | The server's tool definitions load into **every** Claude Code session on your machine, in every repo, whether relevant or not. Docs note each connected server "takes some space in Claude's context window because its tool names and server instructions load into every session." Current CLI versions default to **tool search** (retrieval-based tool discovery rather than dumping every schema up front), which substantially mitigates this, but tool search is disabled with `ENABLE_TOOL_SEARCH=false`, a custom `ANTHROPIC_BASE_URL`, or pre-4.5 models — falling back to a `WaitForMcpServers`-style always-loaded behavior in those cases. Worth confirming which mode you're actually running in via `/context`. |
| stdio vs HTTP | Local-first, no network hop, direct SQLite access, no auth surface to secure | You lose the "share a URL with a teammate" convenience of HTTP; irrelevant for a single-user personal tool. |
| Few consolidated tools vs many granular ones | Matches Anthropic's stated guidance and reduces ambiguous-overlap tool-selection errors | Each tool's parameter surface gets a bit more complex (e.g., `incident_search` takes optional filters instead of having separate `search_by_date`/`search_by_service` tools) — acceptable, and still simpler for the model than choosing among many similar tools. |
| CLAUDE.md hint vs a PreToolUse hook | CLAUDE.md is cheap, low-friction, easy to iterate on | CLAUDE.md/rules are explicitly documented as context, not enforcement — "Claude treats them as context, not enforced configuration... The more specific and concise your instructions, the more consistently Claude follows them," with hooks as the only hard-enforcement mechanism. If you later find Claude Code *silently* skipping Engineering Memory lookups, a hook is the documented escalation path, not a stronger-worded CLAUDE.md line. |
| Relying on tool descriptions (not the `instructions` field) for steering | Confirmed-working channel across sources | Slightly more verbose tool schemas; acceptable given the field's reliability is unverified. |

### Migration Risk

- **CLI surface is actively evolving.** The docs I pulled cite behavior gated behind specific patch versions (e.g., tool-search-by-default, `Failed to connect` detail messages, `paths:` glob budgets, plugin tool-naming format) landing incrementally through the `2.1.x` line — as recently as `v2.1.239` for some fixes referenced in the current docs. Pin nothing to "the CLI behaves like X" without checking `claude --version` / `claude update` periodically; re-verify this integration after CLI upgrades, especially the tool-search vs. always-loaded behavior noted above.
- **Tool schema validation is stricter than plain JSON Schema.** Claude Code currently: (a) requires top-level property names be 1–64 ASCII chars from `A-Z a-z 0-9 _ . -`; (b) flattens root-level `anyOf`/`oneOf`/`allOf` into a single object with a synthesized description rather than passing them through; (c) silently **excludes** any tool whose schema still fails validation after that. Design the Engineering Memory tool schemas as flat objects with simple required/optional fields now, rather than polymorphic/union parameter shapes, or a future stricter validation pass could drop a tool from the surface with no obvious error beyond "server connects but no tools appear."
- **`.mcp.json` / `~/.claude.json` are Claude Code's wiring, not Engineering Memory's data.** This is good news under the project's own rebuildability principle: losing or corrupting these files is a `claude mcp add` away from being fixed and never touches the SQLite DB or evidence files. Don't confuse "MCP registration" with "canonical store" in any backup/restore planning.
- **SSE is deprecated in current transport guidance** — if any future MCP tooling you adopt (e.g., a hosted companion service) defaults to SSE examples found in older blog posts, prefer `--transport http` instead.
- **The `instructions`-field behavior is unsettled.** It's a real 2026-07-28-spec field, current write-ups describe Claude Code's system prompt as including an "MCP Server Instructions" section, but there's an open, unresolved report that a sibling Anthropic client (Claude Desktop) stores-but-never-reads it. Recompiling any part of the integration flow (agent behavior contracts, tests-for-prompting) against that field before its consumption is confirmed in the CLI specifically would be premature; re-check this if/when the GitHub issue closes.
- **Plugin-style tool naming is a different scheme than a plain user/project-scoped server.** If this MCP server is ever repackaged as a Claude Code *plugin* rather than a directly-registered server, its callable tool name changes shape (`mcp__plugin_<plugin-name>_<server-name>__<tool-name>` instead of `mcp__<server-name>__<tool-name>`), which would break any hard-coded tool-name references in skills, permission rules, or subagent `tools:` allow-lists written against the current direct-registration name.

---

Sources:
- [Connect to MCP servers - Claude Code Docs](https://code.claude.com/docs/en/mcp-quickstart)
- [MCP reference - Claude Code Docs](https://code.claude.com/docs/en/mcp)
- [How Claude remembers your project (CLAUDE.md / memory) - Claude Code Docs](https://code.claude.com/docs/en/memory)
- [Writing effective tools for AI agents — Anthropic Engineering](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Prompts - Model Context Protocol spec (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)
- [Claude Desktop: Consume MCP server `instructions` field — anthropics/claude-code issue #43749](https://github.com/anthropics/claude-code/issues/43749)

---

## Backup, Portability & Migrations

### Recommendation

**Export format: a single zip with `manifest.json` at the root, a `core.db` SQLite snapshot, content-addressed attachments, and derived indexes treated as optional/best-effort.**

```
engineering-memory-export-2026-09-06T140000Z.zip
├── manifest.json                     # required, read first, describes everything else
├── core.db                           # SQLite snapshot (canonical DB)
├── attachments.sha256.txt            # BagIt-style payload manifest for evidence files
├── attachments/
│   └── <sha256[0:2]>/<sha256>.<ext>   # content-addressed, dedup'd, byte-identical to originals
├── embeddings/                       # OPTIONAL — included only if small/cheap, never authoritative
│   └── incidents.embeddings.parquet
└── indexes/                          # OMITTED by default — always cheaper to rebuild than restore
    └── (fts5 / vector index dumps, if ever included)
```

Design tenets that drove this shape:

1. **`core.db` and `attachments/` are the only things that must round-trip perfectly.** Everything under `embeddings/` and `indexes/` is a cache of data already in `core.db` and is explicitly marked non-authoritative in the manifest.
2. **Attachments are content-addressed** (`sha256(bytes)` as filename), so re-exporting an incident that references an already-exported file costs zero extra bytes and a copy is trivially verifiable — the filename *is* the checksum.
3. **`manifest.json` is the single source of truth for "can I trust this archive and what's inside it."** Import always reads it first, before touching `core.db`.

`manifest.json` shape:

```jsonc
{
  "manifest_version": "1.0",           // format of this manifest file, semver
  "app_name": "engineering-memory",
  "app_version": "0.4.2",
  "created_at": "2026-09-06T14:00:00Z",
  "created_by": "agrp4@profen.com",

  "schema": {
    "alembic_revision": "a1b2c3d4e5f6",      // exact head at export time
    "min_supported_revision": "9f8e7d6c5b4a" // oldest rev this app build can migrate from
  },

  "database": {
    "path": "core.db",
    "engine": "sqlite",
    "sqlite_lib_version": "3.53.4",
    "backup_method": "vacuum_into",           // vacuum_into | backup_api
    "journal_mode_at_export": "wal",
    "sha256": "e3b0c4...",
    "size_bytes": 18874368,
    "row_counts": { "incidents": 4213, "attachments": 812, "tags": 96 }
  },

  "attachments": {
    "count": 812,
    "total_bytes": 512000000,
    "manifest_file": "attachments.sha256.txt",
    "hash_algorithm": "sha256"
  },

  "derived_indexes": {
    "embeddings": {
      "included": true,
      "model": "text-embedding-3-large",
      "model_version": "2026-01",
      "dims": 3072,
      "built_from_content_hash": "b7e1...",   // hash of the row set the embeddings were built from
      "status": "best_effort"
    },
    "vector_index": { "included": false, "reason": "rebuildable; excluded to save space" },
    "fts5":         { "included": false, "reason": "rebuild is <1s at this scale" }
  },

  "checksums": {
    "algorithm": "sha256",
    "files": {
      "core.db": "e3b0c4...",
      "attachments.sha256.txt": "44136f..."
    }
  }
}
```

**SQLite snapshot safety.** Never back up a WAL-mode database by copying `core.db` alone, and never copy it while a writer might be mid-transaction — the live data can be sitting in `-wal`/`-shm`, and a plain `cp` mid-write can yield a torn, corrupt file. Use one of SQLite's two *hot*, transactionally-consistent snapshot mechanisms instead, both of which fold the WAL back into a single ordinary file with no sidecar needed:

| Mechanism | How | When to use |
|---|---|---|
| `VACUUM INTO 'core.db'` | One SQL statement; SQLite writes a compacted, defragmented copy | Default for on-demand "export now" — simple, and the compaction is a nice side benefit for a DB that accumulates deletes/edits over years |
| Online Backup API (`sqlite3.Connection.backup()` in Python's stdlib) | Copies page-by-page under a shared lock, re-reading pages a concurrent writer touches mid-copy, steppable with `pages=N` + `sleep` between steps | Scheduled/background backups where you want to avoid holding one long read transaction, or the DB has grown large enough that `VACUUM INTO`'s single-shot I/O becomes disruptive |

At the 10k-incident scale this project targets, `core.db` stays small (tens of MB), so `VACUUM INTO` is the default and the Backup API is the fallback for a background/incremental backup daemon. Either way, run `PRAGMA integrity_check` and `PRAGMA wal_checkpoint(TRUNCATE)` before export as a cheap sanity pass, and reopen the DB in its normal `journal_mode=WAL` after — the snapshot copy itself is written in rollback-journal mode by default, which is fine since it's a static file, not a live DB.

**Import verification, in order, fail closed at each step:**

1. Verify the zip's own CRC32 (free, built into the zip format) — catches transport corruption.
2. Read `manifest.json`; refuse to proceed if `manifest_version` is newer than this app build understands.
3. Recompute SHA-256 of `core.db` and of every file in `attachments.sha256.txt`; compare against the manifest. Any mismatch aborts the import before anything is written to the live database — an archive that fails checksum verification must never be partially applied.
4. Open `core.db` read-only and run `PRAGMA integrity_check`.
5. Compare `schema.alembic_revision` against the running app's Alembic head:
   - **older, and ≥ `min_supported_revision`:** run `alembic upgrade head` against the imported copy before use.
   - **older than `min_supported_revision`:** refuse; tell the user to import with an older app version first, or offer a manual migration path.
   - **newer than the running app's head:** refuse — never let an old client silently downgrade a newer schema.
6. Only after 1–5 pass, swap the verified copy into place (atomic file rename), never overwrite the live `core.db` in place mid-check.

**Alembic strategy for a long-lived personal database.** As of 2026 the stable line is Alembic 1.18/1.19, which added `pyproject.toml`-based config (source settings in `pyproject.toml`, environment/connection settings stay in `alembic.ini`) and GUID-based revision filenames instead of sequential integers, which matters less here since this project has one linear history and no branch merges to worry about. Concretely:

- **Batch mode is mandatory, not optional.** SQLite has almost no native `ALTER TABLE` support, so `render_as_batch=True` in `env.py` is required for any autogenerate involving column drops, type changes, or constraint changes — Alembic recreates the table under the hood via copy-and-swap.
- **One logical change per migration**, schema-only changes kept separate from data-backfill migrations — a personal DB that's migrated dozens of times over years is much easier to reason about (and roll back) when each revision does one thing.
- **Autogenerate is a draft, always hand-reviewed** — SQLite's loose typing means autogenerate can miss or misfire on type/constraint diffs; every migration is manually diffed against the model before commit.
- **Every migration ships an explicit `downgrade()`** even though downgrades will rarely run in production — it's the cheapest way to make a migration reviewable and testable, and this is a single-writer personal DB where "roll back" really means "restore from the last backup," but having it costs little.
- **Migrations are tested against a copy of a real, aged database**, not just a fresh one — since evidence attachments and incident rows accumulate for years, the migration suite keeps a small "aged fixture" DB (a handful of incidents created across the project's actual schema history) that every new migration must apply to cleanly in CI.

**Derived indexes: rebuild by default, restore only as an optimization.** Embeddings and the vector index are the one place where "restore what was in the archive" is actively risky rather than merely wasteful: vector-search extensions (e.g. `sqlite-vec`) are compiled against a specific SQLite ABI, and a binary built for one SQLite build loading silently with zero registered functions against a different one is a real, current failure mode. Treat them accordingly:

- The FTS5 lexical index and the vector index are **never included in the archive by default** — at 10k incidents, rebuilding FTS5 is sub-second and rebuilding embeddings/vector index is a background job, so shipping bytes for something this cheap to regenerate only adds fragility.
- If a user opts into shipping embeddings (e.g. to avoid re-paying API costs on a slow connection), they're written as plain vectors keyed by content hash, **not** as an opaque vector-index blob — and the manifest records the embedding model, its version, and the hash of the source content they were computed from.
- On import: if `embeddings.model` + `model_version` match the importing environment's configured embedding model **and** `built_from_content_hash` matches a hash recomputed from the imported rows, the embeddings can be loaded directly and the vector index built from them locally (fast — building the index is cheap, computing embeddings from scratch is the expensive part). On any mismatch, missing file, unsupported model, or extension-load failure, the app falls back silently to **queuing a background re-embed job** and serves lexical search in the meantime — this is the concrete instance of the project's "must degrade gracefully" requirement, applied specifically to import.

### Alternatives Considered

- **BagIt (RFC 8493)** — a standards-based digital-preservation packaging format (`data/` payload directory, `manifest-sha256.txt`, `tagmanifest-sha256.txt`, `bag-info.txt`). Considered because it's a real, long-stable IETF spec purpose-built for exactly "checksummed payload + metadata in a portable bag." Rejected as the *primary* format because it's file-oriented and has no native concept of "this payload file is a SQLite database with its own internal versioning" — you'd still need a custom `manifest.json`-equivalent describing schema/Alembic state layered on top, at which point BagIt's contribution shrinks to "yet another checksum listing" alongside the one already in `manifest.json`. Kept as a documented option: the `attachments.sha256.txt` payload-manifest file is deliberately BagIt-manifest-format-compatible, so a user who wants to also wrap the export in a proper Bag for long-term archival can do so with an off-the-shelf `bagit` CLI without the app needing to implement the whole spec.
- **Plain file copy of `core.db` (+ `-wal`/`-shm`) instead of a snapshot API.** Rejected: correct only if the app can guarantee no writer is open during the copy, which a background scheduled backup can't promise, and it forfeits the free compaction `VACUUM INTO` gives for free.
- **`.sql` text dump (`sqlite3 .dump`) as the portable format** instead of a binary `core.db` file. More diffable and human-readable, and trivially future-proof against SQLite file-format changes, but re-import means replaying the dump through `CREATE TABLE`/`INSERT` (slow at 10k+ incidents with attachments metadata, and any custom SQLite extensions like `sqlite-vec` virtual tables don't dump/reload cleanly). Rejected as the default; noted as a good *supplementary* debug-export option, not the round-trip format.
- **Including the vector index / FTS5 index in every export "for completeness."** Rejected per the derived-index reasoning above — it turns a cheap, always-available rebuild into a compatibility surface (extension ABI, model version) that can silently break import, for a saving that doesn't matter at this project's scale.
- **A directory-of-files export (no zip) synced via a folder-sync tool.** Simpler to inspect, but loses atomicity (a partially-synced folder is a half-import with no single failure point to check) and gives up the free CRC32 layer a zip provides. Rejected for the primary export path; the app's data directory itself already *is* this, incidentally, so nothing is lost — the zip is specifically the portable, verifiable, single-file artifact for moving between machines or archiving off-site.
- **Squashing/rewriting old Alembic migrations into one baseline** as the DB accumulates history. Considered as a way to keep migration count small over years of personal use; rejected as a default because it destroys the audit trail of *how* a specific user's schema got where it is, which matters more for a single long-lived personal DB (where "what changed and when" is itself sometimes diagnostically useful) than it does for a fleet of short-lived environments. Left as a manual, opt-in maintenance operation rather than automatic policy.

### Tradeoffs

- **`VACUUM INTO` vs Backup API:** `VACUUM INTO` is one statement and always produces a compacted file, but holds a read transaction for the full duration — at millions of rows this would matter, at this project's 10k-incident scale it doesn't. Backup API is steppable and friendlier to a background daemon that must never cause a UI hitch, at the cost of more implementation code (progress callback, retry-on-busy) for a benefit that's marginal at this scale.
- **Content-addressed attachments** dedupe well and make verification trivial (filename == checksum), but mean the attachments directory can't be casually browsed by a human looking for "the screenshot from the March incident" without going through the app or the manifest — acceptable since attachments are evidence storage, not a user-facing file browser.
- **Excluding derived indexes by default** keeps exports small and import robust, at the cost of a slower "cold start" after import — a freshly imported archive has working lexical search immediately but degraded (or absent) semantic search until the background re-embed job catches up. This is a deliberate, explicit tradeoff in line with the project's "AI/embeddings can be unavailable, lexical must always work" requirement.
- **Fail-closed checksum verification** (abort entirely on any mismatch, no partial import) is safer but less forgiving than a "best-effort, import what verifies" mode — chosen because a personal incident-history DB is exactly the kind of data where a silently half-imported, subtly incomplete database is worse than a loud failure the user has to explicitly work around.
- **Explicit `downgrade()` on every migration** costs a small amount of ongoing author effort for a path that will rarely execute in anger (real rollback = restore last backup), but it materially improves migration reviewability, which matters more the longer this single database lives.

### Complexity & Maintenance

- The export/import pipeline is a genuinely new subsystem (`backend/app/services/backup/` already scaffolded, currently empty): zip writing/reading, manifest schema (versioned itself, so `manifest_version` needs its own tiny compatibility table over time), checksum computation, and the import verification state machine. This is moderate, self-contained complexity — no ongoing external dependency beyond Python's stdlib (`zipfile`, `hashlib`, `sqlite3`) and Alembic.
- Alembic itself is low-maintenance day-to-day (author a migration per schema change, review the autogenerated diff, run the aged-fixture test), but batch-mode migrations for SQLite are more verbose to hand-review than a straight `ALTER TABLE` would be on a server DB — budget for that when estimating migration PRs.
- The derived-index compatibility check (model name/version/content-hash matching) adds a small, bounded amount of logic that must be kept in sync whenever the embedding model or vector index library changes — worth encoding as a single small "index compatibility" module rather than scattering the checks, since it's exactly the kind of thing that silently rots if touched in two places.
- Long-term, the main ongoing cost is **test-fixture upkeep**: the aged-fixture DB used to validate both migrations and import/export needs to be regenerated (or incrementally extended) each time the schema changes, or it stops exercising real migration history and just tests the latest revision against itself.

### Migration Risk

- **Greatest risk is a schema migration silently corrupting or dropping data on a database the user cannot easily reconstruct** — unlike a server fleet, there's no "just restore from another replica"; the backup archive *is* the only safety net. This is why every migration's test gate includes running it against the aged fixture and diffing row counts/spot-checked content before/after, not just checking that the migration "applies without error."
- **Batch-mode table rebuilds are the single riskiest operation class** on SQLite — a copy-and-swap that gets a constraint or default value subtly wrong can pass `alembic upgrade` cleanly while quietly mistranslating data (e.g., a `NOT NULL` added without a backfill step failing on existing NULL rows only at swap time, or a dropped column silently discarding user-entered evidence notes). Mitigated by requiring an explicit backfill migration *before* any tightening-constraint migration, never combined into one step.
- **Version-skew risk on import**: an archive exported by a much newer app version than the one importing it. Mitigated by refusing (not attempting) any import where the manifest's schema revision is ahead of the importing app's head — the alternative (attempting a "best-effort" partial read of a newer schema) is far riskier than telling the user to update first.
- **Downgrade risk is deliberately treated as out-of-scope for the automated pipeline** — Alembic `downgrade()` methods exist for review/testing purposes, but the shipped recommendation for "I need to go back" is restore-the-last-verified-backup, not `alembic downgrade`, because a downgrade that reintroduces a dropped column or loosens a constraint on live data is a much harder operation to make lossless than restoring a known-good snapshot.
- **Long-lived-history risk**: as migration count grows over years, CI time and cold-`upgrade head` time on a fresh checkout grow with it. Not a correctness risk today at expected migration counts for a personal project, but flagged as the trigger condition for the (currently rejected, opt-in) migration-squashing maintenance operation described above, should the history ever grow large enough to matter.

---

**Sources:**
- [SQLite User Forum: Hot backup database in WAL mode by copying](https://sqlite.org/forum/forumpost/2ea989bbe9)
- [Ensuring Consistent Backups in SQLite WAL Mode Without Disrupting Writers](https://sqlite.work/ensuring-consistent-backups-in-sqlite-wal-mode-without-disrupting-writers/)
- [SQLite Release 3.53.3 / 3.53.4 releaselog](https://sqlite.org/releaselog/3_53_3.html)
- [SQLite backup — Mikael Ståldal's technical blog (VACUUM INTO)](https://www.staldal.nu/tech/2025/07/14/sqlite-backup/)
- [Alembic 1.19.1 documentation — Front Matter / Changelog](https://alembic.sqlalchemy.org/en/latest/front.html)
- [Alembic — Running "Batch" Migrations for SQLite and Other Databases](https://alembic.sqlalchemy.org/en/latest/batch.html)
- [alembic 1.18.4 — Libraries.io](https://libraries.io/pypi/alembic)
- [sqlite-vec ABI mismatch issue reports (openclaw, basic-memory)](https://github.com/basicmachines-co/basic-memory/issues/735)
- [RFC 8493 — The BagIt File Packaging Format (V1.0)](https://www.rfc-editor.org/rfc/rfc8493.html)

---

## Backend Stack

### Recommendation

| Component | Version (current, 2026-09-06) | Notes |
|---|---|---|
| Python | **3.13.15** (pin this; 3.14.7 is current stable but see Migration Risk) | LTS-style safety net for the ML/embedding deps this app will grow into |
| FastAPI | **0.141.1** | `requires-python >=3.10` |
| Pydantic | **2.13.5** (2026-08-28) | v2, Rust `pydantic-core` |
| pydantic-settings | **2.15.0** | env/`.env`-driven config |
| SQLAlchemy | **2.0.52** (2.x async ORM) | do **not** take 2.1 yet — see Migration Risk |
| Alembic | **1.19.2** | `requires-python >=3.10` |
| aiosqlite | **0.22.1** | async SQLite driver |
| greenlet | **3.5.5** | pulled in transitively; required for SQLAlchemy's async bridge |
| Dependency/project manager | **uv 0.12.10** | replaces pip/venv/pyenv/poetry for this project |
| pytest | **9.1.1** | `requires-python >=3.10` |
| pytest-asyncio | **1.4.0** | async test fixtures |
| httpx | **0.28.1** | `ASGITransport` test client |
| uvicorn | **0.52.4** | ASGI server |
| fastapi-cli | **0.0.32** | gives `fastapi dev` / `fastapi run` |
| ruff | **0.16.6** | lint + format, one tool |

**Project layout** — `src/` layout, feature-first, not layer-first-only:

```
pyproject.toml          # PEP 621 metadata, uv-managed
uv.lock
.python-version         # "3.13"
src/
  engmem/
    main.py             # app factory + lifespan (engine startup/shutdown)
    core/               # settings (pydantic-settings), logging, db session dep
    db/
      base.py           # DeclarativeBase
      session.py        # async_sessionmaker, get_db() dependency
    incidents/          # feature package: router.py, models.py, schemas.py, service.py
    evidence/
    search/             # lexical (always-on) + optional embeddings/vector adapters
    alembic/
      env.py
      versions/
tests/
  conftest.py           # in-memory/temp-file aiosqlite engine fixture per test
  incidents/
```

Rationale: with one SQLite file as the canonical store, org by *feature* (incidents, evidence, search) rather than by *layer* (routers/, models/, schemas/) keeps each vertical slice's SQLAlchemy models, Pydantic schemas, and router together — easier to reason about when the DB is the source of truth and everything else must be rebuildable from it.

**Testing**: `pytest` + `pytest-asyncio` (or `anyio` marker) for async fixtures; `httpx.AsyncClient(transport=ASGITransport(app=app))` to drive the FastAPI app in-process without a real socket; a fresh `sqlite+aiosqlite:///:memory:` (or temp-file, if you need to test file-based PRAGMAs like WAL) engine per test module, tables created via `Base.metadata.create_all` or by running Alembic migrations against a temp file to also test the migration chain itself.

**Packaging / single-command run**: keep it a plain installable package, not a bundled executable — this is a CLI/MCP-integrated tool, not a GUI app needing PyInstaller.
```toml
[project]
requires-python = ">=3.13"
dependencies = ["fastapi", "sqlalchemy[asyncio]>=2.0,<2.1", "alembic", "pydantic-settings", "aiosqlite", "uvicorn"]
[dependency-groups]
dev = ["pytest", "pytest-asyncio", "httpx", "ruff"]
```
Then the whole local dev loop is:
```
uv sync
uv run alembic upgrade head
uv run fastapi dev src/engmem/main.py
```
`uv` installs the pinned Python itself if it's missing, so a fresh clone needs only `uv` on `PATH` — no separate pyenv/venv step.

---

### Alternatives Considered

- **Python version**: 3.14.7 (current stable, released 2025-10-07, free-threaded build now officially supported per PEP 779) vs 3.13.15 vs 3.12. Rejected 3.12 as needlessly old for a new project. Considered 3.14 as "most current," but embedding/vector dependencies this system will lean on (onnxruntime, torch-based sentence-transformer stacks) were still catching up on 3.14 wheels as of its release and had open compatibility issues into 2026 — a real risk for the AI/embeddings layer even though the DB/API layer itself is pure-Python and 3.14-clean.
- **Dependency manager**: `uv` vs Poetry vs pip-tools vs plain pip+venv. Poetry (2.4.3) is mature and still viable; pip-tools is minimal but leaves env/Python-version management to you. Chose uv for speed, built-in Python version management (`uv python install`), and single-tool coverage (resolver, venv, lockfile, `uvx` for one-off tools) — the best fit for a local-first tool where "clone and run" matters more than publishing to PyPI.
- **ORM/validation combo**: SQLModel (unifies SQLAlchemy models + Pydantic schemas) was considered to cut boilerplate. Rejected as primary: it trails SQLAlchemy 2.0/Pydantic v2 feature support and couples your DB schema class 1:1 with your API schema, which fights the "SQLite is the canonical source of truth, everything else derived" design — you often want a DB model shape that differs from what you expose over the API/MCP tools.
- **Web framework**: Litestar (also async, arguably cleaner DI) and Flask (sync, simpler) considered. FastAPI chosen for its maturity, MCP/Claude-Code tooling ecosystem familiarity, automatic OpenAPI (useful for generating MCP tool schemas), and native Pydantic v2 integration.
- **Async vs sync DB access on SQLite specifically**: considered a sync SQLAlchemy engine run via `run_in_threadpool`, since SQLite serializes writes regardless of driver. Kept async (`aiosqlite` + `AsyncSession`) per the request, since it composes cleanly with FastAPI's own async request handling and keeps a future Postgres/multi-user path open without a rewrite — but this is a real judgment call, not a clear-cut win (see Tradeoffs).

### Tradeoffs

- **Async SQLAlchemy over SQLite buys you less than it would over Postgres.** SQLite has a single-writer model; async concurrency mainly helps avoid blocking FastAPI's event loop on I/O, not true parallel writes. You still get real value (non-blocking reads under concurrent MCP + web UI usage) but should set `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout` explicitly, and avoid long-held write transactions.
- **Pydantic v2's Rust core** gives large validation-speed wins but means C-extension-style install friction on unusual platforms/architectures (rare in practice now, `pydantic-core` ships broad manylinux/musllinux/arm64 wheels) — acceptable given how central Pydantic is to FastAPI schemas and MCP tool argument validation.
- **uv is younger and more opinionated** than Poetry/pip; it's Astral-maintained (same team as ruff), which is a plus for a unified toolchain but concentrates tooling risk in one vendor. Mitigated by keeping `pyproject.toml` in plain PEP 621 form (no Poetry-only `[tool.poetry]` sections), so switching to pip/Poetry later is a mechanical, not structural, change.
- **Feature-first layout** reads slightly less "textbook FastAPI" than routers/models/schemas layering, but matches how an incident-knowledge system actually changes over time (new evidence types, new search backends) — layer-first optimizes for a codebase that's mostly CRUD, not one with a rebuildable-derived-index architecture.

### Complexity & Maintenance

- **Migration discipline**: every schema change needs an Alembic revision generated via `alembic revision --autogenerate` and hand-checked (autogenerate misses some SQLite-specific changes, e.g. column type narrowing needs batch mode — `alembic.op.batch_alter_table`, since SQLite can't `ALTER COLUMN` directly). This is the main recurring maintenance cost of choosing a "real" migration tool over ad hoc SQL scripts, and it's worth it precisely because the SQLite file is canonical and must survive schema evolution.
- **Session lifecycle**: async engine created once in the FastAPI `lifespan` context, `async_sessionmaker` handed out per-request via a `get_db()` dependency, never a global scoped session — this is a one-time setup cost, low ongoing burden.
- **Lockfile hygiene**: `uv.lock` is committed; `uv sync` is deterministic. Low maintenance — mainly `uv lock --upgrade` on a cadence you choose.
- **Two schema surfaces**: keeping SQLAlchemy models and Pydantic schemas separate (rejecting SQLModel) means writing a small mapping layer per feature. More files, but keeps the DB-is-truth / API-is-a-view boundary explicit, which matters for a "must be rebuildable" architecture — this is a deliberate, ongoing but small tax.

### Migration Risk

- **SQLAlchemy 2.1 is imminent, not yet stable** — `2.1.0rc1` shipped 2026-08-31 (days before this writing), after `2.1.0b1`/`b2` earlier in 2026, targeting a stable release around end-of-summer 2026. **Pin `sqlalchemy[asyncio]>=2.0,<2.1`** now and treat the eventual `2.1.0` as a deliberate, tested upgrade later — it changes typing internals and Core/ORM idioms (dataclass-oriented mapping, stricter typing) that are worth adopting once stable, but not worth chasing a release candidate for a knowledge-base tool where correctness matters more than novelty.
- **FastAPI has not reached 1.0** (currently `0.141.1`) — no formal stable-API guarantee, though breaking changes are rare and well-documented in release notes. Pin an exact version in `uv.lock`, bump deliberately, and lean on your test suite (httpx + pytest-asyncio) as the real safety net rather than semver promises.
- **Python 3.14 adoption for the AI/embeddings side is the biggest real risk**, not the API layer. If/when you add local embedding generation (sentence-transformers, onnxruntime, or similar), verify wheel availability for your exact interpreter before bumping past 3.13 — this is exactly the kind of dependency the "must degrade gracefully without embeddings" requirement exists to protect against, so a Python-version bump should never be allowed to block the lexical-search path.
- **Alembic + SQLite ALTER limitations**: any future migration touching column types/constraints needs batch-mode migrations; this isn't a version-upgrade risk so much as a recurring authoring risk — put it in a CONTRIBUTING note so it isn't rediscovered painfully.
- **uv as build tool**: low risk — it consumes standard `pyproject.toml`/wheel metadata rather than inventing its own format, so even a hypothetical future move away from uv is a tooling swap, not a rewrite.

Sources consulted (PyPI JSON metadata for exact version numbers, plus): [Python 3.14 free-threading (PEP 779) status](https://docs.python.org/3/howto/free-threading-python.html), [SQLAlchemy 2.1.0rc1 blog](https://www.sqlalchemy.org/blog/2026/04/16/sqlalchemy-2.1.0b2-released/), [uv vs Poetry 2026 comparisons](https://pydevtools.com/handbook/explanation/how-do-uv-and-poetry-compare/), [FastAPI async testing docs](https://fastapi.tiangolo.com/advanced/async-tests/), [FastAPI project structure 2026 guides](https://github.com/zhanymkanov/fastapi-best-practices).

---

## Frontend Stack

### Recommendation (with specific version numbers)

Core toolchain, pinned to what's actually stable and interoperable as of September 2026 (not the newest possible number in each ecosystem):

| Concern | Package | Version | Notes |
|---|---|---|---|
| Runtime | Node.js | **24.x** (Active LTS) | Node 22 is Maintenance LTS (EOL Apr 2027); Node 24 is Active LTS through Oct 2026, then LTS to 2028. |
| UI library | React | **19.2.8** | Current major; no 19.3/20 announced. |
| Build tool | Vite | **8.0.9** | Vite 7 (Jun 2025) is EOL'd by 8 (Apr 2026); 8.x is ~5 months mature, safe default. |
| React plugin | `@vitejs/plugin-react-swc` | **4.3.3** | Use for fast dev/HMR. **If you turn on React Compiler, switch to `@vitejs/plugin-react` (Babel) or `oxc-plugin-react-compiler`** — the SWC plugin doesn't run the compiler pass. |
| Language | TypeScript | **6.0.x** (latest final JS-based release) | See Migration Risk — do **not** make 7.0 your primary compiler yet. |
| Styling | Tailwind CSS | **4.3.x** | CSS-first config (`@theme` in CSS, no `tailwind.config.js` needed), `@tailwindcss/vite` plugin, ~5x faster builds than v3. |
| Component primitives | shadcn/ui (on **Base UI 1.0**) | shadcn CLI "Base UI" preset | Base UI became shadcn's default in Jul 2026 (Radix still fully supported, your call — pick one, don't mix). |
| Server-state / data fetching | TanStack Query | **5.102.8** | See rationale below. |
| Client/UI state | Zustand | **5.0.15** | Palette open state, active filters, split-pane/theme — not server data. |
| Routing | TanStack Router | **1.170.32** (`@tanstack/react-router`) | Typed route params + typed search-params fit a search-heavy, deep-linkable app. React Router v7 is the safer-ecosystem alternative (see Alternatives). |
| Command palette | cmdk | **1.1.1** | Headless, Tailwind-native; pair with shadcn's `Command` wrapper. |
| Dense tables | TanStack Table | **9.2.4** | v9 (stable, tree-shakeable) shipped mid-2026. |
| Row virtualization | TanStack Virtual | **3.14.10** | Needed once incident lists cross ~1,000–5,000 rows in the DOM. |
| Graph view | `react-force-graph` | **1.48.2** | Canvas/WebGL force-directed layout — better fit for an incident/entity relationship graph than a diagram-editor library. |
| Compiler optimization | React Compiler | **1.0** (stable Oct 2025) | Auto-memoization; enable from day one on a greenfield codebase. |

**Data-fetching pattern**: TanStack Query owns all server state (incidents, search results, graph edges, AI summaries) against the local FastAPI backend; Zustand owns transient client UI state only. Because the backend is local and must degrade gracefully:
- Give AI/embedding-backed queries (`useVectorSearch`, `useAiSummary`) `retry: false` and a short `staleTime`, so a down embedding service fails fast instead of retry-storming — the lexical-search query stays independent and always resolves.
- Model "search" as one query keyed on `[query, filters]` with `keepPreviousData`/`placeholderData` so typing doesn't flash empty states; the same query is reused by the Ctrl/Cmd+K palette so the two views share one cache.
- Route loaders (TanStack Router) prefetch into the Query cache; components then read via `useQuery`, giving you instant back/forward navigation without duplicate fetches — this is TanStack Router's main advantage over React Router v7 for this app.

**Command palette pattern**: cmdk is unstyled and doesn't virtualize internally — fine for this scale because the palette should never render all 10,000+ incidents. Feed it the same debounced, server-side lexical/vector search result (top 20–50 hits), not the full dataset; render it as a `Dialog`-hosted overlay bound to a global `mod+k` `keydown` listener that toggles a small Zustand `isPaletteOpen` slice.

**Table pattern**: TanStack Table for headless column/sort/filter logic + TanStack Virtual for row windowing once an incident list view exceeds roughly 1,000 rows — pair with `position: sticky` headers and memoized cell renderers (React Compiler handles most of this automatically now).

**Graph pattern**: keep the graph view behind a small adapter component (`<IncidentGraph nodes edges onSelect />`) that wraps `react-force-graph` internally, so the rendering library is swappable later without touching call sites.

### Alternatives Considered

- **Routing** — React Router v7 (framework mode, absorbed Remix): bigger ecosystem, easier hiring/onboarding, natural path to SSR if this ever leaves "local-first desktop-like app." Rejected as primary because this app has no SSR need and its main routing pain point (typed, deep-linkable search filters and incident IDs) is exactly what TanStack Router does better.
- **Command palette** — kbar: better fit if the palette is mostly app-wide *actions* (keyboard shortcuts, command execution) rather than a search-and-navigate box; also has built-in virtualization. Rejected because this app's Ctrl/Cmd+K is primarily "jump to incident / run a query," which is cmdk's sweet spot, and shadcn ships first-class cmdk styling. Mantine Spotlight rejected — pulls in the whole Mantine component system.
- **Component layer** — plain Radix UI or Headless UI, hand-styled: more control, but shadcn/ui (copy-in components, not an npm dependency) gives the same accessible primitives with Tailwind styling already wired up and no runtime lock-in.
- **Client state** — Jotai: better when state is naturally atomic/derived; rejected in favor of Zustand's simpler store model since this app's client state (palette, filters, layout) is a handful of named concerns, not a graph of derived atoms. Redux Toolkit rejected outright — unnecessary ceremony for an app whose real state complexity lives in TanStack Query, not client state.
- **Graph view** — `@xyflow/react` (React Flow): more actively released (12.11.6, days-old at time of writing) and excellent if the graph should feel like an editable flowchart/diagram builder; weaker at large, physics-laid-out relationship graphs. Cytoscape.js (via `react-cytoscapejs`): more layout algorithms (dagre, cose, breadthfirst) and built-in graph analysis (centrality, clustering) — worth revisiting if "Engineering Memory" later wants computed graph metrics (e.g., "most central root cause"), at the cost of a heavier, more imperative API.
- **Type checker** — TypeScript 7.0 (native Go compiler, `tsgo`): 8–12x faster builds, but rejected as the primary compiler today (see Migration Risk).

### Tradeoffs

- **TanStack-everything (Query + Router + Table + Virtual) vs. best-of-breed per concern**: consolidating on one vendor's ecosystem buys consistent APIs, shared TypeScript inference patterns, and known integration points (e.g., Router loaders → Query cache) — at the cost of coupling to one org's release cadence and design opinions across four packages instead of one.
- **shadcn/ui's copy-in model vs. a real npm dependency**: you own the component source outright (easy to adapt for a dense, keyboard-first UI) but updates are a manual diff-and-merge, not a semver bump — there's no automatic security-patch path for these files.
- **cmdk's low churn** (no release in ~12 months) is a maintenance positive (stable, unlikely to break) but means slower response if a React 19/Compiler-specific bug ever surfaces — mitigate by keeping the integration thin (a single wrapper component).
- **TanStack Router's typed search-params** are the right fit for a search/filter-heavy app, but the team pays a steeper learning curve and a smaller Stack Overflow/tutorial corpus than React Router.
- **React Compiler on by default**: eliminates hand-written `useMemo`/`useCallback` almost entirely (real win in a dense table + graph + palette UI where re-render cost matters), but it enforces the Rules of React at build time — any existing custom hook or render-prop pattern that quietly violated them will now surface as a lint/build error. On a greenfield codebase this is a non-issue; it becomes work only if third-party components later imported into this app don't play by the rules.

### Complexity & Maintenance

This stack is intentionally narrow: one meta-framework-free build (Vite), one component-styling system (Tailwind + shadcn), and one data/routing vendor (TanStack) covering server state, routing, tables, and virtualization. That minimizes the number of independent upgrade cadences to track — realistically Vite, React, TypeScript, Tailwind, and "TanStack" as one bloc, plus three small standalone libraries (Zustand, cmdk, react-force-graph). None of these require a backend/framework lock-in (no Next.js, no Remix), which matches a local-first tool that should keep working indefinitely without a hosted vendor. The main ongoing maintenance cost is shadcn/ui's copy-in components: budget periodic manual re-syncs against upstream rather than expecting `npm update` to carry them forward. `react-force-graph` is the one dependency furthest from the React "mainstream" (smaller team, physics-engine internals) — isolate it behind the adapter component mentioned above so its maintenance risk stays contained to one file.

### Migration Risk

- **TypeScript 7.0 is not yet safe as the project's primary compiler.** It ships no stable programmatic API, so `typescript-eslint`, `ts-jest`, and `ts-morph` cannot run on it — `typescript-eslint` closed a TS7-support request as "not planned" for 7.0, and forcing the install crashes ESLint deep in `typescript-estree`. Stay on **TypeScript 6.0.x** as the source of truth for linting/emit; optionally run `tsgo` (TS7's compiler) side-by-side as a fast, non-blocking editor/CI type-check for speed, and re-evaluate switching over at **TypeScript 7.1**, targeted for autumn 2026.
- **Tailwind v4 and React 19 are both mature enough that starting here carries essentially no version risk** — just don't copy Tailwind v3 (`tailwind.config.js`-based) snippets or pre-2025 shadcn examples without adapting them to the CSS-first config.
- **shadcn's Base UI default is only ~2 months old** (July 2026) — most existing shadcn tutorials/blocks online still assume Radix. Both are supported by the CLI, so this is low risk, but commit explicitly to Base UI (or explicitly to Radix) for this project rather than mixing components generated under both defaults.
- **Choosing TanStack Router over React Router v7 is a one-way door in practice** — their loader/data models differ enough that migrating later means rewriting route definitions, not a drop-in swap. Confirm early that the team is comfortable with a TypeScript-first, smaller-ecosystem router before building many routes on it.
- **React Compiler**: low risk for new code, but add `eslint-plugin-react-compiler` to CI immediately so any Rules-of-React violation (most likely to appear in custom TanStack Table cell renderers or cmdk render props) is caught at commit time rather than surfacing as a subtle rendering bug later.

Sources:
- [React Versions – React](https://react.dev/versions)
- [React Compiler v1.0 – React](https://react.dev/blog/2025/10/07/react-compiler-1)
- [Vite 7.0 is out! | Vite](https://vite.dev/blog/announcing-vite7)
- [Releases | Vite](https://vite.dev/releases)
- [Tailwind CSS 4.2 Ships Webpack Plugin, New Palettes and Logical Property Utilities - InfoQ](https://www.infoq.com/news/2026/04/tailwind-css-4-2-webpack/)
- [Tailwind CSS Release Notes & Changelog · August 2026 — releases.sh](https://releases.sh/tailwind-css)
- [tanstack/react-query - npm](https://www.npmjs.com/package/@tanstack/react-query)
- [cmdk vs kbar vs Mantine Spotlight 2026 — PkgPulse Guides](https://www.pkgpulse.com/guides/cmdk-vs-kbar-vs-mantine-spotlight-2026)
- [TanStack Router vs React Router v7: Which Should You Use in 2026?](https://devtoolbox.blog/tanstack-router-vs-react-router-v7-2026/)
- [Microsoft Releases TypeScript 7.0 with a Native Go Compiler, Delivering 10x Faster Builds - InfoQ](https://www.infoq.com/news/2026/08/typescript-7-released/)
- [Why Angular, Vue, and ESLint Can't Upgrade to TypeScript 7.0 (Yet) — DEV Community](https://dev.to/the-modern-web/why-angular-vue-and-eslint-cant-upgrade-to-typescript-70-yet-and-why-ts-71-changes-441g)
- [Why Your TypeScript 7 Upgrade Broke ESLint, ts-jest, and ts-morph — Dev Encyclopedia](https://devencyclopedia.com/blog/typescript-7-broke-eslint-ts-jest-ts-morph)
- [TypeScript 6.0 Ships as Final JavaScript-Based Release — Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/03/23/typescript-6-0-ships-as-final-javascript-based-release-clears-path-for-go-native-7-0.aspx)
- [TanStack Table V9: Taking Form | TanStack Blog](https://tanstack.com/blog/tanstack-table-v9-taking-form)
- [High volumes of data - best practices · TanStack/table Discussion #2392](https://github.com/TanStack/table/discussions/2392)
- [State Management in 2026: Zustand vs Jotai vs Redux Toolkit vs Signals - DEV Community](https://dev.to/jsgurujobs/state-management-in-2026-zustand-vs-jotai-vs-redux-toolkit-vs-signals-2gge)
- [February 2026 - Unified Radix UI Package - shadcn/ui](https://ui.shadcn.com/docs/changelog/2026-02-radix-ui)
- [July 2026 - Base UI as the Default - shadcn/ui](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default)
- [React Graph Visualization Guide: Libraries, Best Practices & Implementation](https://cambridge-intelligence.com/blog/react-graph-visualization-library/)
- [react-force-graph - Libraries.io](https://libraries.io/npm/react-force-graph)
- [@xyflow/react - npm](https://www.npmjs.com/package/@xyflow/react)
- [Node.js 22 vs Node.js 24 in 2026 — PkgPulse Guides](https://www.pkgpulse.com/guides/nodejs-22-vs-nodejs-24-2026)
- [@vitejs/plugin-react-swc - npm](https://www.npmjs.com/package/@vitejs/plugin-react-swc)
- [React Compiler 1.0 + Vite 8: The Right Way to Install](https://recca0120.github.io/en/2026/04/14/react-compiler-vite-v6/)

---

