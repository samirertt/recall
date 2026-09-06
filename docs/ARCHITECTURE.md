# Architecture — Engineering Memory

See [`docs/RESEARCH.md`](RESEARCH.md) for the full research/decision record behind every
choice below. This document describes how those decisions compose into one system.

## 1. Core principle

**SQLite (`data/engineering.db`) + original evidence files (`data/attachments/`) are the
canonical source of truth. Everything else is a derived artifact that must be fully
rebuildable from that pair:**

| Derived (rebuildable) | Canonical (never rebuilt, only backed up) |
|---|---|
| FTS5 lexical index | `incidents`, `attempts`, `environments`, `projects`, `technologies`, `tags` rows |
| Embeddings + vector index | `incident_relations`, `technology_relations`, `incident_technologies`, `incident_projects` |
| Extracted attachment text (OCR/PDF text) | Original attachment bytes on disk |
| AI-generated fields (title, root_cause, tags, ...) | `raw_problem` / `raw_solution` (user's original text) |
| Knowledge-graph traversal caches (if ever added) | `extraction_provenance` (audit trail of what AI claimed and why) |

Every module in this system is designed so that **deleting its derived data and re-running
one command reproduces it** — `rebuild-index`, `rebuild-embeddings`, `reprocess-attachments`.
No feature is allowed to become a second, undeclared source of truth (this is why a
dedicated vector DB, a graph DB, and LanceDB/Chroma-style embedded stores were all
rejected in research — see [RESEARCH.md](RESEARCH.md)).

## 2. Module map

```
                              USER
                               │
                  ┌────────────┴─────────────┐
                  │                          │
                  ▼                          ▼
            WEB APPLICATION              CLAUDE CODE
         (React/Vite/Tailwind)         (CLI + MCP server,
                  │                     stdio, user-scope)
                  │ HTTP (FastAPI)           │
                  └────────────┬─────────────┘
                               ▼
                    ┌─────────────────────┐
                    │   Service Layer      │  <- ONE implementation of
                    │  (app/services/*)    │     "search", "create incident",
                    └──────────┬──────────┘     etc. Both REST and MCP call it.
       ┌───────────────────────┼───────────────────────┐
       │                       │                       │
       ▼                       ▼                       ▼
 Incident/Graph          Retrieval Engine           AI Service
 Service (CRUD,               │                (AIProvider: Claude /
 relations, projects,   ┌─────┴──────┐          OpenAI-compat / local /
 technologies, tags)    │            │           heuristic fallback)
       │                ▼            ▼                  │
       │             FTS5        NumPy vector       Embedding Service
       │           (lexical,   table (semantic,   (fastembed / bge-small,
       │          always-on)   optional/degradable)  optional/degradable)
       │                │            │
       └────────┬───────┴────────────┘
                ▼
         ┌─────────────┐
         │   SQLite     │  ◄── data/engineering.db (WAL mode)
         │ (canonical)  │
         └──────┬──────┘
                │
                ▼
      Attachments Service
      (upload, extract text,
       OCR, PDF) → data/attachments/<incident-id>/
```

Everything below the "Service Layer" line runs **in-process**, inside the one FastAPI
application — no additional services to deploy, monitor, or keep in sync. The MCP server
is a separate OS process (required by the stdio transport) but imports and calls the
exact same service-layer Python modules and SQLAlchemy session factory as the FastAPI
app — there is no second implementation of any business logic behind MCP.

## 3. Data flow: capture → retrieval

```
CAPTURE                    STRUCTURE                 STORE            RETRIEVE
--------                   ---------                 -----            --------
"What happened?"     →     AI extraction        →    incidents   →    FTS5 (always)
"How solved?"               (async, optional,         + attempts       + vector search
[Attach files]               never blocks save)       + provenance      (if available)
     │                            │                        │                │
     ▼                            ▼                        ▼                ▼
raw_problem              title, root_cause,          FTS5 trigger      hybrid fusion
raw_solution              solution, tags,             fires on          (exact + lexical
(saved immediately,       technologies,               INSERT/UPDATE     + vector +
 synchronously)           environment, lessons                          relationship)
     │                    (each field tagged                                 │
     ▼                     basis: explicit/                                 ▼
Attachments saved          inferred/synthesized)                    ranked results +
(original bytes                    │                                "why it ranked" +
 untouched, extraction              ▼                                environment diff
 async & best-effort)      Duplicate detection
                           (search existing before
                            committing new; never
                            auto-merge)
```

Key invariant: **the left column (raw capture) always succeeds synchronously and
independently of AI, embeddings, or OCR.** Everything to its right is best-effort and
asynchronous. This is what "must degrade gracefully" means concretely (Section 52 of the
source spec) — see the per-module degradation notes below.

## 4. Database schema (Phase 2 detail)

Core tables (SQLAlchemy models under `backend/app/models/`), grouped by role:

**Canonical entities**
- `incidents` — `raw_problem`, `raw_solution` (sacred, never overwritten), plus AI-derived
  `title`, `normalized_problem`, `symptoms`, `root_cause`, `solution`, `explanation`,
  `why_solution_worked`, `lesson_learned`, `status`, `severity`, `confidence`, timestamps.
- `attempts` — ordered, per-incident: `action`, `command`, `hypothesis`, `result`, `why_failed`.
- `environments` — structured (OS, hardware, GPU, driver/library versions, runtime, container).
- `projects`, `technologies`, `tags` — reusable reference entities.
- `attachments` — filesystem metadata (`path`, `mime_type`, `sha256`, `size_bytes`), never the bytes themselves.
- `commands` — first-class, copyable, separate from free-text (Section 49).
- `revisions` — versioned knowledge; old solutions are superseded, never deleted (Section 43).

**Relationship (knowledge graph) tables** — see [RESEARCH.md § Knowledge Graph](RESEARCH.md#knowledge-graph):
- `incident_technologies`, `incident_projects` — bipartite many-to-many.
- `incident_relations` — typed, self-referencing (`related_to`, `caused_by`, `solved_by`,
  `supersedes`, `duplicate_of`), `source` (human/ai_suggested) + `confidence`.
- `technology_relations` — typed, self-referencing (`depends_on`, `part_of`, `replaces`).

**Derived/provenance tables** (safe to truncate and rebuild):
- `extraction_provenance` — per-field `basis`, `confidence`, `evidence_quote`, `extractor_model`, `prompt_version`.
- `chunk_embeddings` — `BLOB` vectors + `model_name`/`model_revision`/`dims`, keyed to incident/chunk.
- `extracted_text` — OCR/PDF/log extraction output, keyed to `attachment.sha256`, tagged with `extractor_engine`/`version`/`confidence`.
- `incidents_fts`, `incidents_fts_trigram` — FTS5 virtual tables, trigger-synced, never queried for canonical data.

All schema changes go through Alembic with **batch mode mandatory** (SQLite can't
`ALTER TABLE` most changes natively) and an aged-fixture-DB migration test — see
[RESEARCH.md § Backup, Portability & Migrations](RESEARCH.md#backup-portability--migrations).

## 5. Retrieval pipeline (Phase 7-9 detail)

1. **Query understanding** — split into facets (technology/project/environment), candidate
   exact identifiers (regex library: error codes, ticket IDs, stack-trace classes), free text.
2. **Parallel candidate generation**, each independently degradable:
   - Exact match (`incident_identifiers` + trigram FTS) — always on.
   - Lexical (FTS5 `bm25()`) — always on, this is the floor.
   - Semantic (embed query → NumPy cosine top-k against `chunk_embeddings`) — skipped if
     no embedding model is loaded; this is the one link in the chain allowed to be absent.
   - Relationship expansion (`incident_relations`, low weight, fixed slot count) — always on.
3. **Normalize** each signal to [0,1] within the candidate set (min-max).
4. **Weighted linear fusion** (default weights: exact 0.35, lexical 0.30, vector 0.25,
   relationship 0.10) — **auto-renormalizes** when a signal is degraded/absent (e.g. no
   embedding model ⇒ vector weight redistributes, not zeroed silently). RRF selectable as
   an alternate fusion mode.
5. **Exact-match short-circuit** — a verbatim error-code/ticket-ID match is pinned to the top.
6. Every result carries a `signals` breakdown + `degraded: {vector_search: bool}` so the
   frontend can render "why this ranked highly" (Section 70) and be honest about degraded mode.
7. Reranking (cross-encoder) is **explicitly deferred past v1** — architected as a pluggable
   post-fusion stage, not built now (see RESEARCH.md rationale: not worth the latency/dependency
   at ~10k-incident scale).

Environment-aware retrieval (Section 27): environment similarity is a scoring input, not a
hard filter — a historically similar incident from a different OS/CUDA version still
retrieves, but the response explicitly flags the environment delta rather than hiding it.

## 6. AI & embedding abstractions (Phase 8, 10 detail)

- `AIProvider` (Protocol): `is_available()`, `extract(text, evidence) -> ExtractionResult`.
  Concrete adapters: `ClaudeProvider`, `OpenAICompatibleProvider` (covers self-hosted/Ollama/
  vLLM too), `HeuristicProvider` (zero-LLM fallback: filename/first-line title, keyword-dict
  tags, everything else left null + `needs_ai_review=true`). One shared `IncidentExtraction`
  Pydantic schema is the single source of truth for all three.
- Every extracted field carries `basis` (`explicit`/`inferred`/`synthesized`/`unknown`).
  Hard-fact fields additionally require a verbatim `evidence_quote`, fuzzy-matched
  post-hoc against the raw incident text (`rapidfuzz`) to catch hallucination
  deterministically rather than trusting self-reported confidence.
- `EmbeddingProvider` abstraction: default `fastembed` + `bge-small-en-v1.5`. Every stored
  embedding is tagged `model_name` + `model_revision` + `dims`; a mismatch against the
  configured model blocks semantic search for those rows and flags them for
  `rebuild-embeddings` rather than silently mixing incompatible vector spaces.

## 7. Attachment pipeline (Phase 6 detail)

`Attachment saved (original bytes, untouched) → sniff type → route to extractor →
write extracted_text row`. Every stage is caught and never propagates to the incident-save
transaction (Section 52). Extractors: `charset-normalizer` (text/logs/code), `PyMuPDF` +
`pypdf` fallback (PDF, with OCR fallback for image-only pages), `Tesseract 5`/`pytesseract`
(screenshots, with dark-mode inversion + confidence scoring). All behind one
`extract(attachment) -> ExtractionResult` interface so engines are swappable without
touching ingestion or the "must not block save" guarantee.

## 8. Backup / portability format (Phase 14 detail)

```
engineering-memory-export-<timestamp>.zip
├── manifest.json          # read first; describes everything else; schema/alembic revision,
│                          # checksums, embedding model/version, row counts
├── core.db                # SQLite snapshot via VACUUM INTO (never a raw file copy under WAL)
├── attachments.sha256.txt # BagIt-compatible payload manifest
├── attachments/<sha[:2]>/<sha>.<ext>   # content-addressed, dedup'd
└── embeddings/            # OPTIONAL, best-effort only — never authoritative
```

Import verification is fail-closed and ordered: zip CRC → manifest version check → SHA-256
recompute of every file → `PRAGMA integrity_check` → Alembic revision compatibility check →
atomic swap. Derived indexes (FTS5, vector index) are **never included by default** —
rebuilding is cheaper and safer than restoring a binary index built against a possibly
different SQLite/extension ABI.

## 9. MCP / Claude Code integration (Phase 12 detail)

- Official `mcp` Python SDK (v2 line), `stdio` transport, registered at **user scope**
  (`claude mcp add --scope user`) so it's available from any project, not just this repo.
- ~8-12 consolidated tools (`search_engineering_knowledge`, `get_incident`,
  `get_related_incidents`, `search_by_error_code`, `search_by_technology`,
  `search_by_project`, `find_previous_solution`, `get_engineering_history`,
  `create_incident`, `update_incident`) sharing the FastAPI layer's Pydantic schemas —
  one implementation of business logic behind both surfaces.
- `search_engineering_knowledge` internally degrades exactly like the web search endpoint
  (FTS5-only if embeddings unavailable) and reports which modes ran in `structuredContent`.
- Destructive tools (`update_incident`, any delete/merge) use `ctx.elicit()` confirmation
  as defense-in-depth, since the MCP spec explicitly says `destructiveHint` is advisory only.
- Steering via tool descriptions + a short (1-3 line) `~/.claude/CLAUDE.md` hint — **not**
  the MCP `instructions` field, whose consumption by Claude Code is unverified as of this
  writing (see RESEARCH.md § Claude Code Integration).

## 10. Failure/degradation matrix (Section 52, made concrete)

| Component down | What still works |
|---|---|
| AI provider (Claude/OpenAI/local) unreachable | Incident saves via `HeuristicProvider`; `needs_ai_review=true`; lexical search unaffected |
| Embedding model / vector index unavailable | Hybrid retrieval degrades to exact + lexical + relationship only; `degraded.vector_search=true` in every response |
| Tesseract/OCR unavailable | Attachment saves; text/PDF-native extraction still runs; screenshot text simply isn't searchable until reprocessed |
| FTS5 somehow unusable (wrong SQLite build) | Detected at startup (`PRAGMA compile_options`); falls back to `LIKE` scan; fixed by pinning `pysqlite3-binary` |
| MCP server process not running | Web UI and CLI unaffected; only Claude Code integration is unavailable |
| Vector index corrupted | `rebuild-embeddings` regenerates from canonical text; no data lost |

## 11. What is explicitly deferred (not built in v1)

Per RESEARCH.md, deliberately deferred rather than built now, each with a stated trigger
for revisiting:
- Cross-encoder reranker (trigger: real usage shows fused ranking is insufficient).
- `sqlite-vec` (trigger: NumPy brute-force search becomes measurably slow — not expected under ~50-100k rows).
- Learned/LTR score fusion (trigger: thousands of logged queries accumulate).
- Dedicated/embedded graph database (trigger: never, at stated 10k-incident scale — see RESEARCH.md).
- Streamable HTTP MCP transport (trigger: a networked/multi-device mode is explicitly requested).
