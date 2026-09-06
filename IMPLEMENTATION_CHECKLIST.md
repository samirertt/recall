# Implementation Checklist — Engineering Memory

Persistent memory of the implementation process. Update this file continuously.
Legend: `[ ]` not started · `[~]` in progress · `[x]` completed · `[!]` blocked

Read this file, `docs/RESEARCH.md`, `docs/ARCHITECTURE.md`, and `git log` first at the
start of any new session — do not rely on conversational memory.

---

## PHASE 0 — Repository & Research
- [x] Inspect repository (empty, no commits, git remote `origin` → github.com/samirertt/recall)
- [x] Inspect git status / remotes / branches
- [x] Confirm no existing architecture to preserve (greenfield project)
- [x] Research SQLite & FTS5
    Result: two external-content FTS5 tables (prose + trigram), trigger-synced, BM25 ranking. See docs/RESEARCH.md.
- [x] Research local embeddings
    Result: BAAI/bge-small-en-v1.5 (384-dim, MIT) via fastembed (ONNX, no PyTorch dependency).
- [x] Research vector index options
    Result: NumPy brute-force cosine table for v1; sqlite-vec as the upgrade path if ever needed.
- [x] Research hybrid retrieval & reranking
    Result: weighted linear fusion (exact/lexical/vector/relationship), RRF as alt mode, no reranker in v1.
- [x] Research MCP
    Result: official `mcp` Python SDK v2, stdio transport, ~8-12 consolidated tools.
- [x] Research Claude Code integration
    Result: `claude mcp add --scope user`, short CLAUDE.md hint, tool descriptions over the `instructions` field.
- [x] Research AI provider abstraction
    Result: Protocol-based AIProvider (Claude/OpenAI-compatible/local/heuristic), per-field provenance + fuzzy evidence-quote verification.
- [x] Research attachment ingestion & OCR
    Result: charset-normalizer + PyMuPDF/pypdf + Tesseract 5/pytesseract, all pluggable and non-blocking.
- [x] Research backup/portability & migrations
    Result: zip (manifest.json + core.db via VACUUM INTO + content-addressed attachments); derived indexes excluded by default.
- [x] Research knowledge graph modeling
    Result: plain relational SQLite tables, typed self-referencing edges, WITH RECURSIVE traversal — no graph DB.
- [x] Research backend stack (versions)
    Result: Python 3.13, FastAPI 0.141, Pydantic 2.13, SQLAlchemy 2.0.52 (<2.1), Alembic 1.19, uv.
- [x] Research frontend stack (versions)
    Result: React 19.2, Vite 8, TypeScript 6.0, Tailwind 4.3, TanStack Query/Router/Table/Virtual, shadcn/ui, cmdk.
- [x] Research retrieval evaluation methodology
    Result: hand-labeled synthetic benchmark w/ confusable pairs, P@K/Recall@K/MRR + pairwise env-accuracy, pytest gate.
- [x] Create docs/RESEARCH.md
- [x] Create docs/ARCHITECTURE.md
- [x] Create IMPLEMENTATION_CHECKLIST.md
- [x] Commit research/planning milestone

## PHASE 1 — Architecture
- [x] Define modules & data flow — docs/ARCHITECTURE.md § 2-3
- [x] Define API boundaries — docs/ARCHITECTURE.md § 2 (service layer shared by REST + MCP)
- [x] Define database schema (entities from Section 9 of the source spec) — docs/ARCHITECTURE.md § 4
- [x] Define retrieval pipeline (lexical + vector + exact + metadata + graph → fusion → rerank) — § 5
- [x] Define AI provider abstraction — docs/ARCHITECTURE.md § 6
- [x] Define embedding provider abstraction — docs/ARCHITECTURE.md § 6
- [x] Define attachment storage layout — docs/ARCHITECTURE.md § 7
- [x] Define backup/export archive format — docs/ARCHITECTURE.md § 8
- [x] Define MCP tool interface — docs/ARCHITECTURE.md § 9
- [x] Commit architecture (same commit as research)

## PHASE 2 — Database
- [x] SQLite setup (WAL mode, foreign keys on)
    Result: app/db/session.py sets PRAGMA foreign_keys/journal_mode/busy_timeout on connect.
- [x] SQLAlchemy models
    Result: 17 mapped tables under backend/app/models/, SQLAlchemy 2.0 typed style, TYPE_CHECKING-guarded cross-file relationships (no circular imports).
- [x] Alembic migrations wired up
    Result: async env.py, batch mode mandatory, sqlalchemy.url sourced from app.core.config.Settings (one source of truth). Initial migration includes FTS5 virtual tables + sync triggers co-located with the incidents table DDL.
- [x] Incident (+ raw_problem/raw_solution sacred fields, status/severity/confidence, needs_ai_review)
- [x] Attempt (ordered, per-incident)
- [x] Environment (one-to-one with Incident)
- [x] Project / Technology / Tag (reference entities)
- [x] Attachment (+ ExtractedText derived table)
- [x] Command (first-class, copyable)
- [x] Relationship tables — incident_technologies, incident_projects, incident_tags, incident_relations (typed, self-referencing), technology_relations
- [x] Revision (versioned knowledge)
- [x] AI/embedding provenance metadata — ExtractionProvenance, ChunkEmbedding
- [x] Model/migration tests
    Result: backend/tests/test_migrations.py (upgrade/downgrade round-trip, FTS5 insert/update/delete sync) + test_models.py (relationships, recursive knowledge-graph traversal, CHECK constraint). 7/7 passing, ruff clean.

## PHASE 3 — Backend
- [x] FastAPI app scaffold — backend/app/main.py (app factory, CORS)
- [~] CRUD endpoints (incidents, projects, technologies, tags)
    Result: full incident CRUD (create/get/list/patch/archive) shipped. Projects/technologies/tags have no dedicated CRUD routes yet — created implicitly via get-or-create when attached to an incident (services/incidents/service.py::_get_or_create). Standalone list/rename endpoints for them are still open.
- [x] Request/response validation (Pydantic) — backend/app/schemas/{incident,search}.py, kept separate from SQLAlchemy models by design (docs/RESEARCH.md § Backend Stack)
- [~] Centralized error handling — 404s via HTTPException; no global exception handler/error envelope yet (open)
- [x] Service layer wiring — backend/app/services/{incidents,retrieval}/*, shared by API now, MCP later (Phase 12)
- [x] Configuration (env-based, local-first defaults) — backend/app/core/config.py (pydantic-settings, ENGMEM_ prefix)
- [x] Health/doctor endpoint — GET /health (DB path, FTS5 availability, AI/embedding config); full `engkb doctor` richness is Phase 13
- [x] Backend tests
    Result: backend/tests/test_api_incidents.py, test_api_search.py, test_api_health.py — httpx ASGITransport against the real app. 17/17 passing overall, ruff clean. Manually smoke-tested via a real `uvicorn` process (capture → lexical search round-trip confirmed).

## PHASE 4 — Frontend
- [ ] Vite + React + TS + Tailwind scaffold
- [ ] Routing (dashboard / search / incident detail / projects / technologies / graph / settings)
- [ ] API client / data fetching
- [ ] Dashboard view
- [ ] Incident detail view
- [ ] Search view

## PHASE 5 — Incident Capture
- [x] Zero-friction "New Incident" (problem + solution + attach, nothing else required)
    Result: POST /incidents — only raw_problem is required; status auto-set from whether raw_solution is present. Attach-on-create is not wired (Phase 6 attachments not built yet); attaching after create will work once Phase 6 lands.
- [x] Quick Capture (paste raw text, save immediately, async structuring)
    Result: POST /incidents/quick-capture saves raw_text as raw_problem immediately. "Async structuring" is a no-op today — no AI provider is wired up yet (Phase 10); every captured incident is marked needs_ai_review=true via a heuristic title (first non-blank line) so nothing is silently lost, consistent with Section 21.
- [x] Edit incident — PATCH /incidents/{id} (backend/app/schemas/incident.py::IncidentUpdate)
- [x] Archive/obsolete incident (never hard-delete knowledge) — POST /incidents/{id}/archive sets status=obsolete; row is never deleted
- [x] Failed attempts (Attempt entity CRUD) — replace-all via PATCH `attempts` field (ordered)
- [x] Environment capture — PATCH `environment` field (one environment per incident)
- [x] Project association — PATCH `project_names` (get-or-create by name)
- [x] Tags / technology association — PATCH `tag_names` / `technology_names` (get-or-create by name)
- [ ] Basic duplicate-detection UX surfaced in a frontend (backend already returns `possible_duplicates` on create — Section 22 — but there's no UI yet since Phase 4 hasn't started)

## PHASE 6 — Attachments
- [ ] Upload endpoint
- [ ] Filesystem storage layout (`data/attachments/<incident-id>/...`)
- [ ] Attachment metadata in DB
- [ ] Text extraction (logs, text files, code → searchable text)
- [ ] PDF text extraction
- [ ] OCR for screenshots
- [ ] Extracted text feeds search without altering originals
- [ ] Attachment viewer (frontend)
- [ ] Integrity checks (missing/orphaned files)

## PHASE 7 — Search (Lexical)
- [x] FTS5 virtual table + sync triggers (built in Phase 2's migration; see there)
- [~] Exact error/identifier matching boost
    Result: the trigram substring table (incidents_fts_trigram) catches exact identifiers/error codes today, surfaced as a separate signal — but there's no dedicated regex-based identifier extraction/short-circuit yet (docs/RESEARCH.md § Hybrid Retrieval's "exact-match short-circuit"). Deferred to Phase 9 alongside real score fusion.
- [ ] Metadata filtering (project/technology/environment) — not yet exposed on GET /search (open; straightforward to add as query params over the existing join tables)
- [x] BM25-based ranking — column-weighted bm25() over incidents_fts (backend/app/services/retrieval/lexical.py); weights are an initial default, not yet the configurable profile from Phase 9
- [x] Lexical search tests
    Result: backend/tests/test_api_search.py — keyword match, FTS5-special-character safety (`_build_match_query`'s per-token quoting), empty-result handling, ranking sanity. All passing.

## PHASE 8 — Embeddings
- [ ] Embedding provider abstraction
- [ ] Local embedding model integration
- [ ] Embedding storage + model/version metadata
- [ ] Vector index (chosen per RESEARCH.md)
- [ ] `rebuild-embeddings` command
- [ ] Embedding tests

## PHASE 9 — Hybrid Retrieval
- [ ] Lexical retrieval integration
- [ ] Semantic retrieval integration
- [ ] Exact error/code boost integrated into fusion
- [ ] Technology / project / environment-aware scoring
- [ ] Relationship-graph expansion
- [ ] Score fusion (configurable, documented formula)
- [ ] Reranking (if justified by RESEARCH.md)
- [ ] "Why this ranked highly" explanation
- [ ] Retrieval benchmark harness

## PHASE 10 — AI Enrichment
- [ ] Title generation
- [ ] Problem normalization
- [ ] Root cause extraction
- [ ] Solution extraction
- [ ] Lessons-learned extraction
- [ ] Tag / technology detection
- [ ] Environment extraction
- [ ] Duplicate detection on save
- [ ] Confidence + provenance tracking (`generated_by_ai`, `model`, `generated_at`, `user_verified`)
- [ ] Graceful failure (incident always saves even if AI is down)

## PHASE 11 — Knowledge Graph
- [ ] Relationship schema (typed edges: related_to, caused_by, solved_by, supersedes, ...)
- [ ] Related-incidents queries
- [ ] Technology↔technology relationships
- [ ] Project↔incident relationships
- [ ] AI-suggested relationships (non-destructive, confidence-tracked)
- [ ] Graph visualization (frontend)

## PHASE 12 — Claude Code Integration
- [ ] MCP server scaffold
- [ ] `search_engineering_knowledge` tool
- [ ] `get_incident` tool
- [ ] `get_related_incidents` tool
- [ ] `search_by_error_code` tool
- [ ] `search_by_technology` tool
- [ ] `search_by_project` tool
- [ ] `find_previous_solution` tool
- [ ] `get_engineering_history` tool
- [ ] `create_incident` tool
- [ ] `update_incident` tool
- [ ] Claude Code registration/config documented
- [ ] Integration tests

## PHASE 13 — CLI
- [ ] `engkb search`
- [ ] `engkb add`
- [ ] `engkb quick-add`
- [ ] `engkb incident <id>`
- [ ] `engkb list`
- [ ] `engkb export`
- [ ] `engkb import`
- [ ] `engkb rebuild-index`
- [ ] `engkb rebuild-embeddings`
- [ ] `engkb doctor`

## PHASE 14 — Backup / Import / Export
- [ ] Export (manifest.json + db + attachments + embeddings + indexes → zip)
- [ ] Checksums
- [ ] Import with validation
- [ ] Schema-version migration on import
- [ ] Rebuild incompatible derived indexes on import
- [ ] Backup/restore tests

## PHASE 15 — Testing
- [ ] Unit tests (models, validation, parsing, extraction, search, ranking, relationships, import/export)
- [ ] Integration tests (CRUD, search, embedding, AI, export/import, rebuild, MCP/API)
- [ ] End-to-end workflow test
- [ ] Retrieval benchmark results recorded

## PHASE 16 — Security
- [ ] No secrets/credentials logged or committed
- [ ] Path traversal prevention on attachments
- [ ] Upload validation
- [ ] Security review pass

## PHASE 17 — Performance
- [ ] Lexical search < 100ms target
- [ ] Hybrid retrieval < 500ms target
- [ ] Async processing for embeddings/OCR/AI/indexing
- [ ] Basic perf observability (durations logged, no private data)

## PHASE 18 — UX Polish
- [ ] Keyboard shortcuts (Cmd/Ctrl+K, N, Enter, Escape)
- [ ] Command palette
- [ ] Search result "why it ranked" UI
- [ ] Accessibility pass
- [ ] Documentation pass

## PHASE 19 — Final Validation
- [ ] Full scenario: create → structure → store → attach → embed → index → search → retrieve → Claude synthesis → edit → export → delete local copy → import → rebuild → search again
- [ ] Acceptance test: Jetson/CUDA/PyTorch example (Section 76)
- [ ] Acceptance test: portability/export-import (Section 77)
- [ ] Acceptance test: AI failure tolerance (Section 78)
- [ ] Acceptance test: vector index failure/rebuild (Section 79)
- [ ] Acceptance test: Claude Code retrieval (Section 80)

---

## Known limitations / open items
- No frontend yet (Phase 4 not started) — everything so far is verified via the API/pytest and one manual curl smoke test against a real `uvicorn` process.
- No attachments (Phase 6), embeddings/vector search (Phase 8), real hybrid score fusion (Phase 9), AI enrichment (Phase 10), knowledge-graph write paths beyond the DB layer (Phase 11 traversal exists, but no API/UI to create relations yet), MCP/Claude Code integration (Phase 12), CLI (Phase 13), or backup/import (Phase 14).
- Duplicate detection (Section 22) is backend-only right now (`possible_duplicates` on incident creation) — no UI to act on it.
- Projects/Technologies/Tags have no standalone list/rename endpoints — only get-or-create-by-name via incident PATCH.
- `git push` is not possible from this sandboxed dev environment (no HTTPS credential helper or registered SSH key for the `origin` remote) — commits are local only until the user pushes them or authorizes the environment.

## Next actions
1. Phase 6 — Attachments: upload endpoint, filesystem storage, text/PDF/OCR extraction pipeline (docs/RESEARCH.md § Attachment Ingestion & OCR).
2. Phase 4 — Frontend: Vite/React/Tailwind scaffold, capture form, search view, incident detail view (docs/RESEARCH.md § Frontend Stack for exact versions).
3. Phase 8/9 — Embeddings + hybrid retrieval (fastembed/bge-small, NumPy vector table, weighted fusion).
4. Phase 12/13 — MCP server + CLI, so Claude Code can use this knowledge base directly.
_(updated as work proceeds)_
