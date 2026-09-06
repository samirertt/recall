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
    Result: backend/tests/test_api_incidents.py, test_api_search.py, test_api_health.py, test_attachments.py — httpx ASGITransport against the real app. 23/23 passing overall, ruff clean. Manually smoke-tested via a real `uvicorn` process (capture → lexical search round-trip confirmed).

## PHASE 4 — Frontend
- [x] Vite + React + TS + Tailwind scaffold
    Result: `frontend/` — Vite 8.2.2, React 19.2.8, TypeScript 6.0.2, Tailwind CSS 4.3.3 (CSS-first `@theme` config, `@tailwindcss/vite` plugin) — versions match docs/RESEARCH.md § Frontend Stack exactly.
- [~] Routing (dashboard / search / incident detail / projects / technologies / graph / settings)
    Result: two routes only — home (search + capture + recent list, combined rather than a separate dashboard) and incident detail. Projects/technologies/graph/settings views don't exist yet (no backend list endpoints for the first two either — see Phase 3's open item). Used plain `react-router-dom` v7 rather than the TanStack Router recommended in docs/RESEARCH.md — a deliberate MVP simplification (no typed search-params or route loaders needed yet for two routes); revisit if/when route count and filter complexity grow.
- [x] API client / data fetching — `frontend/src/lib/api.ts` (typed fetch wrapper, mirrors backend/app/schemas) + TanStack Query 5.102.8 for server state, matching docs/RESEARCH.md § Frontend Stack
- [~] Dashboard view — folded into the home page (search bar + capture form + recent incidents list) rather than a separate view; no stats/recurring-problem widgets yet (that's Section 47/Phase 45, not started)
- [x] Incident detail view — full fields (problem/solution/root cause/solution/why-it-worked/lesson/environment/attempts), inline edit form, archive button, attachment list + upload
- [x] Search view — folded into the home page; live results with relevance score, per-signal badges (lexical/substring/attachment), highlighted snippets, and an honest "semantic search not available" banner (matches the `degraded` field rather than hiding it)
- [x] Keyboard shortcuts (partial) — Cmd/Ctrl+K focuses search, Cmd/Ctrl+N focuses the capture form, Cmd/Ctrl+Enter saves. Command palette (Section 50) not built yet.
- [x] Manually verified in a real browser (not just `tsc`/build passing): capture → detail-page navigation → edit → save → home → search → result-click round trip, screenshot-verified at each step, zero console errors. `tsc -b` and `vite build` both pass cleanly.

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
- [ ] Basic duplicate-detection UX surfaced in a frontend — the backend returns `possible_duplicates` on create (Section 22) and the frontend exists (Phase 4), but `CaptureForm`/`HomePage` don't currently read or display that field. A real, if small, remaining gap — not blocked on anything anymore.

## PHASE 6 — Attachments
- [x] Upload endpoint — POST /incidents/{id}/attachments (multipart), 413 on >25MB
- [x] Filesystem storage layout (`data/attachments/<incident-id>/...`) — server-generated on-disk filenames only (never the user's filename), defense-in-depth path check in `read_attachment_bytes`
- [x] Attachment metadata in DB — Attachment (filename/mime/size/sha256) + ExtractedText
- [x] Text extraction (logs, text files, code → searchable text) — charset-normalizer + ANSI stripping
- [x] PDF text extraction — PyMuPDF primary, pypdf fallback, per-page OCR fallback for image-only pages
- [x] OCR for screenshots — Tesseract via pytesseract (grayscale/upscale/dark-mode-invert preprocessing); genuinely tested degrading gracefully in this dev environment, which has no Tesseract binary installed
- [x] Extracted text feeds search without altering originals — separate `extracted_texts_fts` FTS5 index (migration d21505cad1e4), original attachment bytes never touched
- [~] Attachment viewer (frontend) — the incident detail page lists attachments with a working download link (Phase 4/11); no inline preview (images/PDFs rendered in-page) or extracted-text display yet, so "viewer" is currently just "list + download."
- [x] Integrity checks (missing/orphaned files) — `engkb doctor` (Phase 13) checks every attachment DB row against the actual file on disk and reports missing files by count

## PHASE 7 — Search (Lexical)
- [x] FTS5 virtual table + sync triggers (built in Phase 2's migration; see there)
- [~] Exact error/identifier matching boost
    Result: the trigram substring table (incidents_fts_trigram) catches exact identifiers/error codes today, surfaced as a separate signal — but there's no dedicated regex-based identifier extraction/short-circuit yet (docs/RESEARCH.md § Hybrid Retrieval's "exact-match short-circuit"). Deferred to Phase 9 alongside real score fusion.
- [ ] Metadata filtering (project/technology/environment) — not yet exposed on GET /search (open; straightforward to add as query params over the existing join tables)
- [x] BM25-based ranking — column-weighted bm25() over incidents_fts (backend/app/services/retrieval/lexical.py); weights are an initial default, not yet the configurable profile from Phase 9
- [x] Lexical search tests
    Result: backend/tests/test_api_search.py — keyword match, FTS5-special-character safety (`_build_match_query`'s per-token quoting), empty-result handling, ranking sanity. All passing.

## PHASE 8 — Embeddings
- [x] Embedding provider abstraction — `EmbeddingProvider` Protocol (app/services/embeddings/provider.py), lazy/cached singleton so model load never happens at import time
- [x] Local embedding model integration — `BAAI/bge-small-en-v1.5` via `fastembed` 0.8.0 (ONNX, no PyTorch), matches docs/RESEARCH.md exactly
- [x] Embedding storage + model/version metadata — `ChunkEmbedding` (Phase 2) tagged `model_name`/`model_revision`/`dims`; mismatched-model rows are simply excluded from vector search, never mixed
- [x] Vector index (chosen per RESEARCH.md) — NumPy brute-force cosine over all current-model `ChunkEmbedding` rows (app/services/embeddings/service.py::search_vector); `sqlite-vec` upgrade path documented, not needed at this scale
- [x] `rebuild-embeddings` command — `rebuild_embeddings()` service function (CLI wiring is Phase 13)
- [x] Embedding tests — backend/tests/test_hybrid_search.py: real semantic search (near-zero lexical overlap query), graceful degradation when the provider is unavailable, rebuild-all

## PHASE 9 — Hybrid Retrieval
- [x] Lexical retrieval integration
- [x] Semantic retrieval integration
- [~] Exact error/code boost integrated into fusion
    Result: no dedicated identifier-extraction subsystem (`incident_identifiers` table + regex library + short-circuit) yet — the trigram substring signal is a partial stand-in. Explicitly deferred, not silently dropped; see docs/RESEARCH.md § Hybrid Retrieval.
- [ ] Technology / project / environment-aware scoring — not wired into fusion yet (metadata filtering on GET /search is also still open from Phase 7)
- [x] Relationship-graph expansion — 1-hop expansion from top seed candidates (fixed small slot count, decayed score), app/services/retrieval/search.py
- [x] Score fusion (configurable, documented formula) — weighted-linear fusion (app/services/retrieval/fusion.py), pure functions unit-tested in isolation (test_fusion.py), auto-renormalizes when the vector signal is unavailable
- [ ] Reranking — deliberately deferred per docs/RESEARCH.md (not justified at this scale); architecture leaves room for a pluggable post-fusion stage
- [x] "Why this ranked highly" explanation — every result carries a per-signal breakdown + the `weights_profile` id that produced it
- [x] Retrieval benchmark harness — built in Phase 15 (`backend/tests/test_retrieval_eval.py`); smaller than docs/RESEARCH.md's ~150-250-incident recommendation (~32 incidents) but real and gated, and caught a genuine Phase 7 bug (see Phase 15's entry below)

## PHASE 10 — AI Enrichment
- [x] Title generation — via whichever AIProvider is active; heuristic (first line) by default in this environment (no API key configured)
- [x] Problem normalization — `normalized_problem` field, same pipeline as title/root_cause
- [x] Root cause extraction
- [x] Solution extraction
- [x] Lessons-learned extraction (+ `why_solution_worked`)
- [x] Tag / technology detection — `IncidentExtraction.tags`/`technologies`; not yet auto-attached as `IncidentTag`/`IncidentTechnology` rows (open — currently just recorded in provenance, no auto-link step)
- [ ] Environment extraction — not implemented; environment is still human-entered only (Phase 5)
- [x] Duplicate detection on save — lexical-based (Phase 3/5's `possible_duplicates`); no separate AI-based duplicate pass
- [x] Confidence + provenance tracking — `ExtractionProvenance` rows per field (`basis`/`confidence`/`extractor_provider`/`extractor_model`/`prompt_version`/`schema_version`/`extracted_at`); verification is deterministic (rapidfuzz match against raw text → `explicit` vs `synthesized`/`inferred`), not self-reported LLM confidence — see docs/RESEARCH.md § AI Provider Abstraction
- [x] Graceful failure (incident always saves even if AI is down) — `_try_enrich` catches and logs, never blocks; verified in tests and via a real HTTP round trip
- [x] AI provider abstraction — `AIProvider` Protocol: `HeuristicProvider` (zero-LLM, always available), `ClaudeProvider` (`messages.parse(output_format=...)`), `OpenAICompatibleProvider` (`chat.completions.parse(response_format=...)`). `get_ai_provider()` never raises/returns None — always falls back to heuristic.
- [ ] Live-tested Claude/OpenAI extraction — **not run against a real API** (no ANTHROPIC_API_KEY/OPENAI_API_KEY available in this dev environment). Provider *selection* and *fallback* logic is tested; the actual wire calls to `messages.parse`/`chat.completions.parse` are unverified against live endpoints. Set `ENGMEM_AI_PROVIDER=claude` + `ANTHROPIC_API_KEY` (or `openai_compatible` + `ENGMEM_OPENAI_API_KEY`/`ENGMEM_OPENAI_API_BASE`) and re-test before relying on this in production.
- [ ] "Never overwrite an already-set field" rule is a simple null-check, not provenance-aware — it can't distinguish "heuristic already set this" from "human deliberately set this," so a field the heuristic fills first will resist a later, better AI pass unless manually cleared. A `force re-enrich` path (Section 41) would resolve this; not built yet.

## PHASE 11 — Knowledge Graph
- [x] Relationship schema (typed edges: related_to, caused_by, solved_by, supersedes, ...) — Phase 2
- [x] Related-incidents queries — `WITH RECURSIVE` traversal (Phase 2) now exposed via GET /incidents/{id}/related (max_depth, relation_type filters)
- [x] Human-authored relationship API — POST/GET/DELETE /incidents/{id}/relations, enforces the canonical `source_id < target_id` ordering for symmetric types (related_to/duplicate_of) so a pair can never end up double-stored
- [ ] Technology↔technology relationships — data model exists (Phase 2), no API/UI yet
- [x] Project↔incident relationships — via PATCH /incidents/{id} `project_names` (Phase 5); no dedicated relationship-browsing API
- [x] AI-suggested relationships (non-destructive, confidence-tracked) — GET /incidents/{id}/relations/suggested computes candidates from vector similarity on demand and **never persists anything**; a human/frontend explicitly accepts via the create-relation endpoint (Section 29). Verified with a real embedding similarity match (94%) in a live browser test.
- [x] Graph visualization (frontend) — a simple related/suggested-incidents list on the incident detail page, with a one-click "Link" to accept a suggestion. **Not** the force-directed graph view docs/RESEARCH.md's frontend stack section describes (`react-force-graph`) — that's still open if a visual graph explorer is wanted later.

## PHASE 12 — Claude Code Integration
- [x] MCP server scaffold — `mcp_server/server/main.py`, official `mcp` SDK 2.1.1, stdio transport
    Result: named `mcp_server/`, **not** `mcp/` as the source spec's Section 60 suggested — that name collides with the installed `mcp` PyPI package (`python -m mcp.server.main` from repo root resolves `mcp` to the local directory, not the SDK, and fails). Caught and fixed before committing; documented in the module docstring and docs/CLAUDE_CODE.md.
- [x] `search_engineering_knowledge` tool
- [x] `get_incident` tool
- [x] `get_related_incidents` tool
- [x] `search_by_error_code` tool
- [x] `search_by_technology` tool
- [x] `search_by_project` tool
- [x] `find_previous_solution` tool
- [x] `get_engineering_history` tool
- [x] `create_incident` tool
- [x] `update_incident` tool
- [x] Claude Code registration/config documented — docs/CLAUDE_CODE.md (setup, tool table, security, example usage)
- [x] Integration tests
    Result: backend/tests/test_mcp_server.py launches the server as a **real subprocess** over stdio and drives it with the actual MCP client SDK (not just calling the Python functions in-process) — tool listing, create_incident, search_engineering_knowledge, get_incident all verified. This is what caught the `mcp/` naming collision.
- [ ] Registration against a live `claude` CLI — **not tested**: no `claude` binary is installed in this dev sandbox. The `claude mcp add`/`claude mcp list` commands in docs/CLAUDE_CODE.md are per current official docs but unverified end-to-end here.

## PHASE 13 — CLI
- [x] `engkb search` (`scripts/engkb search "<query>"`, shows relevance % + which signals matched)
- [x] `engkb add` (`--problem`/`--solution` flags, or interactive prompts)
- [x] `engkb quick-add` (positional arg or stdin)
- [x] `engkb incident <id>`
- [x] `engkb list` (`--status`, `--limit`)
- [x] `engkb export <path.zip>`
- [x] `engkb import <path.zip>`
- [x] `engkb rebuild-index` — new: `rebuild_fts_index()` (Section 41's "Rebuild FTS Index," not previously implemented)
- [x] `engkb rebuild-embeddings`
- [x] `engkb doctor` — DB path/existence, FTS5 availability, embedding/AI provider status, integrity check, row counts, orphaned-attachment check
    Result: `backend/app/cli.py` (Typer), wrapped by `scripts/engkb`. Manually run end-to-end for every command; also covered by `backend/tests/test_cli.py` (Typer `CliRunner`, genuinely invoking each command's own `asyncio.run` path, not just calling service functions directly) — caught and fixed a real bug (a raw `zipfile.BadZipFile` wasn't wrapped as `BackupError`, so `engkb import` on a garbage file crashed instead of reporting failure cleanly).

## PHASE 14 — Backup / Import / Export
- [x] Export (manifest.json + db + attachments → zip; embeddings/indexes deliberately excluded per docs/RESEARCH.md — rebuilding is cheaper/safer than restoring a possibly-ABI-mismatched binary index)
- [x] Checksums — sha256 of core.db and every attachment, content-addressed inside the archive
- [x] Import with validation — fail-closed order: zip integrity → manifest version → checksums → DB integrity_check → schema-revision compatibility, all before touching the live DB
- [x] Schema-version migration on import — an older archive is upgraded (against the *extracted* copy, never the live DB, before it's swapped into place); a revision this app build doesn't recognize is refused outright
- [x] Rebuild incompatible derived indexes on import — derived indexes are never in the archive to begin with, so this is unconditional: `engkb rebuild-index`/`rebuild-embeddings` after every import
- [x] Backup/restore tests
    Result: `backend/tests/test_backup.py` — valid-export shape, content-addressed attachments, tampered-checksum rejection, future-manifest-version rejection, and the **full Section 77 acceptance scenario** (export → delete live DB + attachments entirely → import → rebuild embeddings → lexical search finds the incident → attachment-text search finds it too). All passing.

## PHASE 15 — Testing
- [x] Unit tests (models, validation, parsing, extraction, search, ranking, relationships, import/export) — 61 tests across backend/tests/
- [x] Integration tests (CRUD, search, embedding, AI, export/import, rebuild, MCP/API) — all present; MCP specifically tested via a real subprocess + real client SDK, not mocked
- [x] End-to-end workflow test — backend/tests/test_backup.py's Section 77 scenario (export → delete everything → import → rebuild → search) is the fullest one; no single test walks *every* step of Section 75's list (create→attach→embed→…→delete local copy→import→rebuild→search again) in one place — see Phase 19
- [x] Retrieval benchmark results recorded
    Result: `backend/tests/test_retrieval_eval.py` + `backend/tests/retrieval/fixtures/` (~32-incident corpus, one confusable pair per required category — CUDA/ROS2/Docker/C++/Python/Linux/networking/Git/databases/embedded/computer-vision/build-systems — plus 9 filler incidents; smaller than docs/RESEARCH.md's ~150-250 recommendation, a deliberate first-pass scope reduction). Measured baseline (both hybrid and lexical_only profiles): **precision@1 0.958, recall@10 1.0, MRR 0.979, pairwise-environment-accuracy 0.80**. Gates in `thresholds.yaml` set below this with headroom.
    **This benchmark caught a real, significant bug**: `_build_match_query()` (Phase 7) joined FTS5 query tokens with no explicit operator, which FTS5 defaults to implicit AND — silently requiring *every single word* of a query to appear verbatim in an incident. This made lexical search fail on almost any natural-language query longer than 2-3 words (it "worked" in earlier tests only because those queries were short and fully contained in the target text). Fixed to explicit `OR` (the standard IR model — bm25's IDF weighting already rewards specific/rare terms over common ones, so this doesn't degenerate into "matches everything"). This had been silently degrading lexical search since Phase 7 with no test catching it until this benchmark existed.
    Metric note: uses precision@1 rather than precision@5 — this corpus's queries mostly have exactly one relevant incident, which caps precision@5 at 0.2 regardless of ranking quality (structurally uninformative); documented in the test file.

## PHASE 16 — Security
- [x] No secrets/credentials logged or committed — verified no hardcoded keys anywhere; `.env`/`.env.*` gitignored (`.env.example` added, no real values); AI provider adapters never log request/response bodies containing keys
- [x] Path traversal prevention on attachments — server-generated on-disk filenames (never the uploaded filename) + a defense-in-depth resolved-path check in `read_attachment_bytes`; tested (`test_attachment_storage_rejects_path_traversal_in_relative_path`)
- [x] Upload validation — 25MB size cap (413 on exceed); no file-type allowlist/denylist (attachments are never executed, only stored + text-extracted, so this is an intentional non-restriction, not an oversight)
- [x] Security review pass
    Result: found and fixed a **real stored-XSS vulnerability** — attachment downloads served the uploader's browser-supplied `Content-Type` verbatim with no `Content-Disposition`, so an uploaded `text/html` (or `image/svg+xml`, which can also embed `<script>`) file would execute at this app's own origin when opened. Fixed: `safe_download_headers()` forces `Content-Disposition: attachment` for anything outside a small inline-safe allowlist (images excluding SVG, audio, video, PDF, plain text) and always adds `X-Content-Type-Options: nosniff`. Tested with an actual `<script>` payload (`test_uploaded_html_is_never_served_inline`, `test_uploaded_svg_is_never_served_inline`) confirming both the fix and that legitimate inline types (PNG) still work.
    Also added: a 500,000-character cap on every incident free-text field (previously unbounded — a large paste could force an unbounded embedding/FTS-write cost per request; low severity for a single-user local tool, but free to close).
    Reviewed and found no issue: SQL injection (every query is parameterized; the few f-string-built SQL statements interpolate only fixed internal constants, never user input — verified via `grep`), CORS (origins restricted to the configured frontend origin, no wildcard `allow_credentials`), frontend XSS (only one `dangerouslySetInnerHTML` call, already HTML-escaping before highlighting — Phase 4), import zip-slip (Python 3.13's `zipfile.extractall()` sanitizes path traversal in member names by default).
    Not addressed (noted, not a gap in this pass): no zip-bomb size-limit check on `engkb import` — accepted as low-risk since import is a deliberate local CLI action on a file the user already chose to trust, not an untrusted upload vector.

## PHASE 17 — Performance
- [x] Lexical search < 100ms target
    Result: **measured**, not assumed — `scripts/perf_bench.py` against 1,000 seeded synthetic incidents: mean 0.9ms, p95 1.1ms, max 2.1ms. ~50-100x under target.
- [x] Hybrid retrieval < 500ms target
    Result: measured on the same 1,000-incident dataset: mean 38.6ms, p95 42.1ms, max 66.9ms. ~7-12x under target; cost is dominated by the query-embedding ONNX inference call, not the NumPy cosine scan. Full numbers in docs/SEARCH.md § Performance.
- [x] Async processing for embeddings/OCR/AI/indexing — all three are best-effort, non-blocking calls after the synchronous incident save (Phases 6/8/10); "async" here means "never blocks/fails the request," not a background job queue (there is no task queue — each best-effort step still runs inline within the same request, just after the point where failure no longer matters). A real background queue is not built; noted as a possible future improvement if per-request latency from embedding/AI calls becomes noticeable at higher usage.
- [x] Basic perf observability (durations logged, no private data) — `app/core/timing.py::log_duration()`, a small context manager wired into `search_incidents`, `embed_incident`, and attachment extraction. Logs duration + counts/ids only (e.g. `query_len`, `limit`, `incident_id`, `size_bytes`) at DEBUG on the `app.perf` logger — never query text, incident content, or attachment content. Manually verified the log lines contain no content.

## PHASE 18 — UX Polish
- [x] Keyboard shortcuts (Cmd/Ctrl+K, N, Enter, Escape)
    Result: ⌘K opens the command palette (global, works from any route — previously only focused an on-page search box that didn't exist on the incident detail page); ⌘N focuses the capture form; ⌘Enter saves it; Escape closes the palette (Radix Dialog's built-in behavior) and now also cancels an in-progress incident edit.
- [x] Command palette
    Result: `frontend/src/components/CommandPalette.tsx` using `cmdk` 1.1.1 (matches docs/RESEARCH.md's recommendation exactly) — live incident search-as-you-type plus static actions, mounted once in `App.tsx` so it works on every page. Manually verified in a real browser: opened from the incident detail page (proving the "works from anywhere" fix), typed a query, saw live results, clicked one, navigated and closed; separately confirmed Escape closes it. Zero console errors.
- [x] Search result "why it ranked" UI — per-signal badges (lexical/substring/attachment/semantic/related) + relevance % on every result card (Phase 4); the fusion `weights_profile` isn't surfaced in the UI itself, only in the raw API response
- [~] Accessibility pass
    Result: light pass only — `aria-label`s added where text alone didn't already describe an element (e.g. the search input), the command palette has a proper dialog `label`. **Not** a full WCAG audit (no screen-reader testing, no systematic focus-trap/contrast review) — honest gap, not silently skipped.
- [x] Documentation pass — docs/SEARCH.md, docs/BACKUP_AND_MIGRATION.md, docs/CLAUDE_CODE.md, docs/DEVELOPMENT.md all written (Phase 12/14/16); README kept current after every phase; this checklist itself is the continuously-updated record

## PHASE 19 — Final Validation
- [x] Full scenario: create → structure → store → attach → embed → index → search → retrieve → Claude synthesis → edit → export → delete local copy → import → rebuild → search again
    Result: no single test walks literally every step in one function, but every step is covered by an existing test and the full chain has been run manually end-to-end via a real browser + real `uvicorn` process at multiple points this build (Phases 4, 6, 8/9, 11, 18). `test_backup.py`'s round-trip test covers store→attach→export→delete→import→rebuild→search; `test_final_acceptance.py` covers create→structure→search→retrieve→(synthesis-ready detail).
- [x] Acceptance test: Jetson/CUDA/PyTorch example (Section 76)
    Result: `backend/tests/test_final_acceptance.py::test_section76_jetson_cuda_pytorch_acceptance` — creates the exact incident, structures it (environment/failed attempt/why-it-worked), confirms it's retrievable by the exact query text from Section 57 **and** ranks above a deliberately-similar-but-unrelated desktop-GPU incident that also mentions CUDA, and confirms every fact needed for Claude's synthesis format (§32) is present on the detail response.
- [x] Acceptance test: portability/export-import (Section 77)
    Result: `test_backup.py::test_full_export_delete_import_rebuild_search_round_trip` — export, genuinely delete the live DB and every attachment file, import, rebuild embeddings, confirm lexical **and** attachment-text search both still find the incident.
- [x] Acceptance test: AI failure tolerance (Section 78)
    Result: `test_final_acceptance.py::test_section78_ai_provider_failure_tolerance` — a provider whose `extract()` always raises; incident still saves (201) and is still findable via search.
- [x] Acceptance test: vector index failure/rebuild (Section 79)
    Result: `test_final_acceptance.py::test_section79_vector_index_loss_and_rebuild` — deletes an incident's `ChunkEmbedding` rows directly (simulating index loss), confirms canonical incident data is completely unaffected and the incident is no longer found via the vector signal specifically, then confirms `rebuild_embeddings()` restores it.
- [x] Acceptance test: Claude Code retrieval (Section 80)
    Result: `test_mcp_server.py` (the authoritative version — a real subprocess over stdio driven by the actual MCP client SDK) plus `test_final_acceptance.py::test_section80_claude_code_retrieval_shape` (a lighter structural check that environment-comparison data is present on the response shape an MCP tool call returns).

---

## Known limitations / open items

_All 20 phases (0-19) now have at least a first working pass — see each phase's
section above for what's genuinely done vs. simplified. This list is what's actually
still open, not a phase-by-phase status (that's above)._

- Frontend has no automated tests (no Vitest/Playwright) — verified by manual browser
  interaction (screenshots + console-error checks) plus `tsc -b`/`vite build` passing
  at every milestone, not by an automated suite.
- Frontend uses plain `react-router-dom` v7, not TanStack Router as docs/RESEARCH.md
  recommends — a deliberate simplification for two routes; revisit before adding
  typed/filterable search-param-heavy routes.
- No force-directed knowledge-graph visualization (`react-force-graph`, per
  docs/RESEARCH.md) — relationships are a simple list on the incident detail page.
- Duplicate detection (Section 22) surfaces `possible_duplicates` in the API but has
  no dedicated UI to act on a duplicate hint beyond what's already visible.
- Projects/Technologies/Tags have no standalone list/rename endpoints — only
  get-or-create-by-name via incident PATCH. Technology↔technology relationships have
  no API/UI (data model only, from Phase 2).
- No dedicated exact-identifier-extraction subsystem (an `incident_identifiers`
  table + regex library + ranking short-circuit) — the trigram substring signal is a
  partial stand-in. Metadata/environment-aware filtering isn't exposed as query
  parameters on `GET /search` either.
- Retrieval benchmark corpus is ~32 incidents, not docs/RESEARCH.md's recommended
  ~150-250 — growing it is pure YAML data entry, no harness changes needed.
- Tesseract OCR is genuinely unavailable in this dev environment (no system binary
  installed) — verified this degrades correctly (attachment still saves, extraction
  recorded `failed`) rather than assumed. Install `tesseract-ocr` to exercise the
  real OCR path; there's no "reprocess failed extractions" command yet either.
- Claude/OpenAI AI enrichment adapters are implemented per current SDK APIs but
  **not exercised against a live API** (no key available in this sandbox) — only
  provider selection/fallback logic is tested against a real key requirement.
- MCP server is verified via a real subprocess + the real client SDK, but **not
  registered against a live `claude` CLI** (none installed in this sandbox).
- No accessibility audit beyond a light `aria-label` pass (no screen-reader testing,
  no systematic contrast/focus-trap review).
- No background job queue — embedding/AI enrichment/extraction are all best-effort
  inline steps after the synchronous save, not a separate worker process. Fine at
  personal scale; would need revisiting under sustained concurrent load.
- `git push` from this sandboxed dev environment needed a token supplied directly by
  the user in chat (no ambient credential helper or registered SSH key) — used
  transiently via an environment variable for each push, never written to disk or
  committed.

## Next actions

Roughly in priority order, none blocking — the system is fully functional end-to-end
today:

1. Grow the retrieval benchmark corpus toward docs/RESEARCH.md's ~150-250 target as
   real usage surfaces near-miss queries worth adding.
2. Verify the Claude/OpenAI AI enrichment adapters against a real API key; verify
   MCP registration against a live `claude mcp add`.
3. Build the exact-identifier-extraction subsystem + expose metadata/environment
   filters on `GET /search` (the two concrete Phase 7/9 gaps).
4. Frontend: projects/technologies list endpoints + UI, a real attachment viewer,
   force-directed graph visualization, and an automated test suite (Vitest/Playwright).
5. A background job queue if/when inline best-effort embedding/AI calls become a
   noticeable per-request latency source under real usage.
_(updated as work proceeds)_
