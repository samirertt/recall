# Engineering Memory

A local-first personal engineering knowledge system: it remembers technical problems
you've hit, how you investigated them, what you tried that *didn't* work, what
actually solved it, why, and the environment it happened in — so that months or years
later you (or Claude Code) can ask "have I seen this before?" and get a real answer
instead of having to re-debug from scratch.

It is not a notes app, and it is not a thin wrapper around
`text → embedding → vector DB → search`. See [docs/RESEARCH.md](docs/RESEARCH.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full reasoning; the short version
is in [Why it exists](#why-it-exists) below.

> **Status**: every phase of the original spec has at least a first working pass —
> capture, editing, attachments (text/PDF/OCR), AI enrichment, hybrid search (lexical
> + semantic + relationship expansion), a browser UI, a CLI, backup/restore, and an
> MCP server for Claude Code all work end-to-end today, backed by 60 passing tests.
> See [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for exactly what's
> done, what's simplified vs. the original research, and what's still open — it's
> kept up to date after every milestone, not written once and forgotten.

---

## Why it exists

Engineers re-solve the same problems repeatedly because the knowledge from last time
lives, if anywhere, in scattered shell history, closed terminal tabs, or a vague memory
of "I think it was a driver version thing." The goal here is to make *recording* a
solved problem take under a minute, so that the friction between "I solved something"
and "I recorded it" stops being the reason knowledge gets lost.

Four things this system is deliberately built to remember, per incident:

1. **What happened** (the raw problem, verbatim, never rewritten by AI)
2. **What we thought was wrong / what we tried** (failed attempts are first-class data, not noise)
3. **What actually worked**
4. **Why it worked, what environment it worked in, and what evidence supports it**

## Architecture, in one paragraph

**SQLite (`data/engineering.db`) plus the original evidence files on disk are the only
canonical state.** Everything else — the FTS5 lexical index, extracted attachment
text, and the embeddings/vector index — is a derived artifact that can be deleted and
regenerated from the canonical pair without losing anything. This
is why, for example, an OCR engine being unavailable or an embedding model changing
can never corrupt or lose your actual incident history — see
[docs/ARCHITECTURE.md § 10](docs/ARCHITECTURE.md#10-failuredegradation-matrix-section-52-made-concrete)
for the full degradation matrix. Full design rationale, alternatives considered, and
current library/version choices are in [docs/RESEARCH.md](docs/RESEARCH.md).

## Features (what's actually implemented right now)

- **Zero-friction capture** — `POST /incidents` requires only a problem description;
  everything else (environment, tags, technologies, root cause, ...) is optional and
  gets filled in by best-effort AI enrichment right after saving, or later by a human.
- **Quick capture** — `POST /incidents/quick-capture` saves a raw pasted blob
  immediately; structuring happens via the same best-effort enrichment.
- **AI enrichment** — a provider abstraction (Claude / OpenAI-compatible / heuristic
  fallback) fills title/root-cause/solution/lesson-learned, never overwriting a field
  already set by a human. No API key is configured in this dev environment, so the
  zero-LLM heuristic provider is what actually runs — see
  [What's not built yet](#whats-not-built-yet). Every field's provenance is recorded
  (deterministic fuzzy-match against the raw text, not self-reported LLM confidence).
- **Knowledge graph** — link incidents together (`POST /incidents/{id}/relations`),
  browse N-hop related incidents, and see AI-*suggested* relationships computed from
  embedding similarity — suggestions are never auto-created, only proposed for a
  one-click accept.
- **Editing** — `PATCH /incidents/{id}` to add environment details, failed attempts,
  root cause/solution, and project/technology/tag associations (created on the fly by
  name — no separate "create a tag first" step).
- **Archive, never delete** — incidents move to an `obsolete` status; the row and its
  history are never destroyed.
- **Attachments** — upload logs, code, screenshots, or PDFs. Text is extracted
  automatically (plain text/logs, PDF text layers via PyMuPDF with OCR fallback for
  scanned pages, and Tesseract OCR for screenshots) and becomes searchable — the
  *original* file is never modified, and a failed extraction never blocks the upload.
- **Hybrid search** — `GET /search?q=...` fuses four signals: SQLite FTS5 (BM25-ranked
  lexical), a trigram substring index (error codes, partial log lines), local semantic
  embeddings (`BAAI/bge-small-en-v1.5` via `fastembed`, fully offline after the first
  model download), and 1-hop relationship expansion from top matches. Weighted-linear
  fusion auto-renormalizes if the embedding model is ever unavailable — lexical search
  never stops working. Every result reports exactly which signals matched and the
  fusion weights used ("why did this rank highly"). Arbitrary user queries are safe
  against FTS5 query-syntax injection.
- **Possible-duplicate hints** — every new incident is checked against existing ones
  and surfaces close lexical matches (never auto-merged).
- **A working browser UI** — capture, quick-capture, search with relevance/signal
  breakdown and highlighted snippets, incident detail view (edit, attach, archive) —
  see [Quick start](#quick-start).

## What's not built yet

A retrieval evaluation benchmark, a dedicated security/performance audit pass, a
force-directed knowledge-graph visualization, and a command palette. See
[IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for the exhaustive,
continuously-updated list — every phase of the original spec has at least a first
pass implemented at this point.

---

## Installation

Requires [`uv`](https://docs.astral.sh/uv/) (manages the Python version and
dependencies — you don't need Python pre-installed).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from the repo root:

```bash
uv sync                      # installs Python 3.13 + all dependencies
uv run alembic upgrade head  # creates data/engineering.db and applies all migrations
```

`uv sync` also installs the attachment-ingestion dependencies (PyMuPDF, pypdf,
pytesseract, Pillow) and the embedding dependencies (`fastembed`, numpy) by default.
OCR additionally needs the **Tesseract** system binary — it's optional: if it's
missing, image attachments still upload fine, they just won't have searchable
extracted text (this is tested, not assumed — see `backend/tests/test_attachments.py`).
The embedding model (`BAAI/bge-small-en-v1.5`, ~35MB) downloads from Hugging Face on
first use and then runs fully offline; if that first download can't happen (no
network, blocked registry), semantic search silently stays off and lexical search is
unaffected (`GET /health` and every search response's `degraded.vector_search` flag
report this honestly rather than erroring).

```bash
# Debian/Ubuntu
sudo apt install tesseract-ocr
# macOS
brew install tesseract
```

## Quick start

**Backend** (terminal 1):

```bash
uv run uvicorn app.main:app --app-dir backend --reload
```

**Frontend** (terminal 2, first time only needs `npm install`):

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` — the dev server proxies `/api/*` to the backend on port
8000 (see `frontend/vite.config.ts`), so both must be running. From there: type a
problem into the capture form, save it, then search for it (`Ctrl/Cmd+K` focuses
search from anywhere, `Ctrl/Cmd+N` focuses the capture form).

Or drive the API directly:

```bash
# Capture an incident — only raw_problem is required
curl -s -X POST http://127.0.0.1:8000/incidents \
  -H 'Content-Type: application/json' \
  -d '{
        "raw_problem": "Jetson Orin NX stopped detecting CUDA after reinstalling Python packages. PyTorch build was CPU-only.",
        "raw_solution": "Installed the CUDA-enabled PyTorch build for the Jetson."
      }'

# Search for it later
curl -s "http://127.0.0.1:8000/search?q=CUDA%20Jetson" | python3 -m json.tool
```

`GET /health` reports DB path, FTS5 availability, and current AI/embedding
configuration — useful as a first troubleshooting step (a fuller `engkb doctor`
command is planned for the CLI phase).

Interactive API docs (Swagger UI) are available at `http://127.0.0.1:8000/docs` once
the server is running.

## Creating incidents

Two paths, matching how you actually work:

- **"I have a moment to write this up properly"** → `POST /incidents` with
  `raw_problem` (required) and optionally `raw_solution`. Then `PATCH /incidents/{id}`
  whenever you have more structure to add:

  ```bash
  curl -s -X PATCH http://127.0.0.1:8000/incidents/1 \
    -H 'Content-Type: application/json' \
    -d '{
          "root_cause": "CPU-only PyTorch wheel silently installed by a bare pip install.",
          "environment": {"operating_system": "Ubuntu", "os_version": "22.04", "gpu": "Jetson Orin NX"},
          "attempts": [{"action": "Reinstall PyTorch the same way", "result": "Same failure"}],
          "technology_names": ["CUDA", "PyTorch", "Jetson"],
          "tag_names": ["gpu", "embedded"]
        }'
  ```

- **"I just fixed something, let me dump it before I forget"** →
  `POST /incidents/quick-capture` with a single `raw_text` blob. It saves
  immediately (never blocked on anything) and gets a heuristic title from its first
  line, flagged `needs_ai_review: true` until AI enrichment (Phase 10) exists to do
  better.

Attach evidence to either:

```bash
curl -s -X POST http://127.0.0.1:8000/incidents/1/attachments \
  -F "file=@/path/to/screenshot.png"
```

## Searching

```
GET /search?q=<query>&limit=20
```

Returns results ranked by a `fused_score`, each with a `signals` breakdown (which of
lexical/trigram/vector/attachment-text/relationship matched, and how), a top-level
`degraded` object (`vector_search: true` only if the embedding model genuinely
couldn't be loaded — search keeps working lexical-only rather than erroring), and the
`weights_profile` that produced the ranking. See
[docs/ARCHITECTURE.md § 5](docs/ARCHITECTURE.md#5-retrieval-pipeline-phase-7-9-detail)
for the full pipeline design, including what's still simplified (no dedicated
exact-identifier extraction yet; metadata/environment-aware filtering isn't exposed
on the endpoint yet).

## Claude Code integration

An MCP server (`mcp_server/`) exposes ten tools (search, get/create/update incident,
related-incidents traversal, technology/project/error-code search, previous-solution
lookup, history browse) over stdio, backed by the same service modules the REST API
uses. See [docs/CLAUDE_CODE.md](docs/CLAUDE_CODE.md) for setup (`claude mcp add`) and
the full tool list. Verified end-to-end by launching it as a real subprocess and
driving it with the actual MCP client SDK (`backend/tests/test_mcp_server.py`) — not
yet registered against a live `claude` CLI (none is installed in this dev sandbox).

## CLI

`scripts/engkb` works independently of the web UI:

```bash
scripts/engkb add --problem "..." --solution "..."   # or run with no flags to be prompted
scripts/engkb quick-add < paste.txt                   # or: echo "..." | scripts/engkb quick-add
scripts/engkb search "CUDA out of memory"
scripts/engkb list --status unresolved
scripts/engkb incident 42
scripts/engkb export backup-2026-09-06.zip
scripts/engkb import backup-2026-09-06.zip
scripts/engkb rebuild-index          # FTS5, from canonical text
scripts/engkb rebuild-embeddings     # vector index, from canonical text
scripts/engkb doctor                 # DB/FTS5/embedding/AI health, integrity check
```

## Backup

`scripts/engkb export <path.zip>` produces a portable archive: `manifest.json`
(schema revision, checksums, row counts) + a `VACUUM INTO`-produced SQLite snapshot +
content-addressed attachments. Embeddings and the FTS5 index are deliberately
excluded — rebuilding them after import is cheaper and safer than restoring a binary
index that might not match the importing machine's SQLite/extension build. Full
design in
[docs/RESEARCH.md § Backup, Portability & Migrations](docs/RESEARCH.md#backup-portability--migrations).

`scripts/engkb import <path.zip>` is fail-closed: it verifies the zip's own integrity,
the manifest version, every checksum, and schema compatibility — all before touching
your live database — then migrates the imported copy to the current schema if needed.
Run `rebuild-index` and `rebuild-embeddings` afterward. This whole round trip
(export → delete the local DB and attachments entirely → import → rebuild → search)
is exercised as an automated test, not just described —
`backend/tests/test_backup.py::test_full_export_delete_import_rebuild_search_round_trip`.

## Development

Backend:

```bash
uv sync                          # install/update dependencies (Python 3.13, all groups)
uv run alembic upgrade head      # apply migrations to data/engineering.db
uv run uvicorn app.main:app --app-dir backend --reload   # run the API
uv run pytest backend/tests -v   # run the test suite
uv run ruff check backend        # lint
uv run ruff check backend --fix  # lint, auto-fixing what's safe
uv run ruff check backend mcp_server  # lint everything, including the MCP server
```

Tests spin up a real temporary SQLite database per test and run the actual Alembic
migration chain against it (not a `create_all()` shortcut), so migration correctness
is exercised on every run, not just when someone remembers to test it separately.

Frontend:

```bash
cd frontend
npm install         # first time, or after pulling dependency changes
npm run dev         # dev server at http://127.0.0.1:5173, proxies /api to the backend
npm run build        # type-checks (tsc -b) then produces a production build in dist/
```

There is no frontend test suite yet (see IMPLEMENTATION_CHECKLIST.md's open items) —
changes are currently verified by running the dev server and checking in a browser.

Project layout:

```
backend/
  app/
    api/        # FastAPI routers — thin, delegate to services/
    core/       # settings (pydantic-settings, ENGMEM_* env vars)
    db/         # engine/session construction, lazily built (test-friendly)
    models/     # SQLAlchemy 2.0 models — the canonical schema
    schemas/    # Pydantic request/response models — kept separate from models/ on purpose
    services/   # business logic, shared by the REST API, CLI, and MCP server
    cli.py      # `engkb` Typer app
  alembic/      # migrations — batch mode mandatory (SQLite ALTER TABLE limitations)
  tests/
frontend/
  src/
    lib/api.ts      # typed fetch client, mirrors backend/app/schemas
    components/     # SearchBar, CaptureForm, ResultCard
    pages/          # HomePage (search + capture + recent list), IncidentDetailPage
mcp_server/
  server/main.py  # MCP tools — thin wrappers over backend/app/services (stdio transport)
scripts/
  engkb           # thin wrapper: `engkb <command>` without remembering the uv invocation
data/           # gitignored: engineering.db, attachments/, embeddings/, indexes/
docs/
  RESEARCH.md      # decision record: what was chosen, alternatives, tradeoffs, why
  ARCHITECTURE.md  # how those decisions compose into the system
  CLAUDE_CODE.md   # MCP setup/tool reference
IMPLEMENTATION_CHECKLIST.md   # persistent, continuously-updated build status
```

Note the package name `mcp_server/`, not `mcp/` — it collides with the installed `mcp`
PyPI package if named that (see the module's own docstring for how that failure
actually manifests).

Configuration is via environment variables (prefix `ENGMEM_`, see
`backend/app/core/config.py`) or a `.env` file — see `Settings` for the full list
(database path, attachments directory, AI provider, embedding config, CORS origins).
