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
- [~] Commit research/planning milestone

## PHASE 1 — Architecture
- [ ] Define modules & data flow
- [ ] Define API boundaries
- [ ] Define database schema (entities from Section 9 of the source spec)
- [ ] Define retrieval pipeline (lexical + vector + exact + metadata + graph → fusion → rerank)
- [ ] Define AI provider abstraction
- [ ] Define embedding provider abstraction
- [ ] Define attachment storage layout
- [ ] Define backup/export archive format
- [ ] Define MCP tool interface
- [ ] Commit architecture

## PHASE 2 — Database
- [ ] SQLite setup (WAL mode, foreign keys on)
- [ ] SQLAlchemy models
- [ ] Alembic migrations wired up
- [ ] Incident
- [ ] Attempt
- [ ] Environment
- [ ] Project
- [ ] Technology
- [ ] Tag
- [ ] Attachment
- [ ] Relationship
- [ ] Revision
- [ ] AI/embedding provenance metadata
- [ ] Model/migration tests

## PHASE 3 — Backend
- [ ] FastAPI app scaffold
- [ ] CRUD endpoints (incidents, projects, technologies, tags)
- [ ] Request/response validation (Pydantic)
- [ ] Centralized error handling
- [ ] Service layer wiring
- [ ] Configuration (env-based, local-first defaults)
- [ ] Health/doctor endpoint
- [ ] Backend tests

## PHASE 4 — Frontend
- [ ] Vite + React + TS + Tailwind scaffold
- [ ] Routing (dashboard / search / incident detail / projects / technologies / graph / settings)
- [ ] API client / data fetching
- [ ] Dashboard view
- [ ] Incident detail view
- [ ] Search view

## PHASE 5 — Incident Capture
- [ ] Zero-friction "New Incident" (problem + solution + attach, nothing else required)
- [ ] Quick Capture (paste raw text, save immediately, async structuring)
- [ ] Edit incident
- [ ] Archive/obsolete incident (never hard-delete knowledge)
- [ ] Failed attempts (Attempt entity CRUD)
- [ ] Environment capture
- [ ] Project association
- [ ] Tags / technology association

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
- [ ] FTS5 virtual table + sync triggers
- [ ] Exact error/identifier matching boost
- [ ] Metadata filtering (project/technology/environment)
- [ ] BM25-based ranking
- [ ] Lexical search tests

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
_(updated as work proceeds)_

## Next actions
_(updated as work proceeds)_
