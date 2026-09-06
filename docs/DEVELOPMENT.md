# Development

Day-to-day reference for working on this codebase. See the [root README](../README.md)
for the product-level quick start; this is the deeper developer reference.

## Installation & environment

Requires [`uv`](https://docs.astral.sh/uv/) — it manages the Python version itself,
so you don't need Python pre-installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync   # installs Python 3.13.15 + every dependency group by default
```

`default-groups` in `pyproject.toml` (`dev`, `ingestion`, `embeddings`, `ai`, `mcp`)
means a plain `uv sync` gives you everything: the test suite, attachment
extraction (PyMuPDF/pypdf/pytesseract), local embeddings (fastembed), the
Claude/OpenAI SDKs, and the `mcp` package for the MCP server. Nothing here needs
network access at runtime except the one-time embedding model download and any
configured cloud AI provider.

```bash
uv run alembic upgrade head    # creates data/engineering.db, applies all migrations
```

## Running things

```bash
# Backend
uv run uvicorn app.main:app --app-dir backend --reload

# Frontend (separate terminal)
cd frontend && npm install && npm run dev

# CLI
scripts/engkb doctor

# MCP server (normally launched by Claude Code, not run directly)
uv run python -m mcp_server.server.main
```

The frontend dev server proxies `/api/*` to the backend on port 8000
(`frontend/vite.config.ts`) — both need to be running for the browser UI to work.

## Tests

```bash
uv run pytest backend/tests -v          # everything
uv run pytest backend/tests/test_cli.py -v          # just the CLI
uv run pytest backend/tests/test_retrieval_eval.py -v -s   # retrieval benchmark, with output
uv run ruff check backend mcp_server            # lint
uv run ruff check backend mcp_server --fix      # lint, auto-fixing what's safe

cd frontend && npx tsc -b && npm run build      # frontend type-check + build
```

Every backend test gets a fresh temp SQLite file with the **real** Alembic migration
chain applied (not a `create_all()` shortcut) via the `configured_db` fixture in
`backend/tests/conftest.py` — migration correctness is exercised on every run.
`backend/tests/test_mcp_server.py` and `test_cli.py` go further and drive the actual
MCP server subprocess / CLI entry point, not just the underlying service functions.

There is no frontend automated test suite yet — frontend changes are currently
verified by running the dev server and checking in a real browser (see
IMPLEMENTATION_CHECKLIST.md's open items).

## Configuring AI enrichment

Default is `heuristic` (no API key, no network call — first-line title only). To use
a real provider, set (via `.env` or environment — see `.env.example`):

```bash
ENGMEM_AI_PROVIDER=claude
ENGMEM_ANTHROPIC_API_KEY=sk-ant-...
# or:
ENGMEM_AI_PROVIDER=openai_compatible
ENGMEM_OPENAI_API_KEY=sk-...          # or leave unset for a local/self-hosted endpoint
ENGMEM_OPENAI_API_BASE=http://localhost:11434/v1   # e.g. Ollama's OpenAI-compatible endpoint
```

`get_ai_provider()` never raises and never blocks capture — if the configured
provider can't initialize (missing key, network unreachable), it silently falls back
to heuristic and the incident still saves normally, flagged `needs_ai_review: true`.

**Not verified against a live API in this project's own dev/test environment** — no
key was available. If you configure a real key, a good smoke test is:
`scripts/engkb add --problem "..." --solution "..."` then `scripts/engkb incident <id>`
and check whether `root_cause`/`lesson_learned`/etc. got populated beyond the
heuristic title.

## Configuring embeddings

On by default — `BAAI/bge-small-en-v1.5` via `fastembed`, downloaded from Hugging
Face on first use (~35MB) and cached for fully offline use afterward. Set
`ENGMEM_EMBEDDINGS_ENABLED=false` to force lexical-only search even if the model
would otherwise load (e.g. on a low-resource machine, or for a fully airgapped setup
before the first download can happen). `GET /health` reports real runtime
availability, not just the config flag.

## Configuring Claude Code

See [docs/CLAUDE_CODE.md](CLAUDE_CODE.md) for the full MCP setup, tool list, and
security notes.

## Building a release

There is no packaged/installable distribution yet (no PyInstaller bundle, no PyPI
package — `package = false` in `pyproject.toml` is deliberate, matching
docs/RESEARCH.md's backend-stack decision: this is a "clone and `uv sync`" tool, not
a published package). "Building a release" today means: tag a commit, and anyone
with the repo runs the Installation steps above. Revisit this if/when the project
needs to be distributed to someone who shouldn't need the source checked out.

## Troubleshooting

- **`No module named mcp.server.main`** — you likely renamed or copied the MCP
  server package back to `mcp/`. It's deliberately named `mcp_server/` because
  `mcp/` collides with the installed `mcp` PyPI package (`python -m mcp.server.main`
  resolves `mcp` to the local directory, not the SDK, since `-m` puts the current
  working directory first on `sys.path`). See the module's own docstring.
- **`sqlalchemy.exc.MissingGreenlet`** — almost always a lazy-loaded SQLAlchemy
  relationship accessed outside an `await`ed session call (e.g. `incident.attempts`
  on an object that wasn't eagerly loaded via `selectinload`, or a freshly-constructed
  object whose collection was never touched pre-commit). Fix by eager-loading the
  relationship in the query, or by re-fetching the object via the service layer's
  `get_incident()` (which already applies the right `selectinload` options) before
  touching it again. Two real instances of this bug were caught and fixed during
  development — see the Phase 3 and Phase 8/10 entries in
  IMPLEMENTATION_CHECKLIST.md.
- **OCR always reports `failed`** — Tesseract is a system binary, not a Python
  package; `uv sync` installs `pytesseract` (the wrapper) but not Tesseract itself.
  Install it (`apt install tesseract-ocr` / `brew install tesseract`) and re-upload,
  or use `scripts/engkb` to reprocess (a dedicated reprocessing command isn't built
  yet — see IMPLEMENTATION_CHECKLIST.md). Everything else keeps working without it.
- **`git push` asks for a password and rejects it** — GitHub no longer accepts
  account passwords for git operations; use a Personal Access Token as the password,
  or switch the remote to SSH with a registered key.
- **A logger stops producing output partway through a test run (or after an
  in-process `alembic upgrade` call), with no error** — Alembic's `env.py` calls
  `logging.config.fileConfig()`, whose default `disable_existing_loggers=True`
  silently sets `.disabled = True` on every already-created logger not named in
  `alembic.ini`'s logging config. Since migrations run on every test (via the
  `configured_db` fixture) and inside `engkb import` when a schema upgrade is
  needed, any application logger instantiated *before* that point loses all output
  for the rest of the process. Fixed with `disable_existing_loggers=False` in
  `backend/alembic/env.py` — if you add a new logger and its output mysteriously
  vanishes only when run alongside other tests (never in isolation), this is almost
  certainly why.
- **Alembic autogenerate misses a change, or a migration touching a column
  type/constraint fails** — SQLite has almost no native `ALTER TABLE` support;
  `render_as_batch=True` is already set in `backend/alembic/env.py`, but always
  hand-review an autogenerated migration rather than trusting it blindly (SQLite's
  loose typing means autogenerate can misfire on type/constraint diffs).
