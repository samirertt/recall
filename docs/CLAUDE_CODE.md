# Claude Code Integration

See [docs/RESEARCH.md § MCP Integration](RESEARCH.md#mcp-integration) and
[§ Claude Code Integration](RESEARCH.md#claude-code-integration) for the full decision
record. This document is the practical setup/reference guide.

## Architecture

The MCP server (`mcp_server/server/main.py`) is a thin tool layer over the exact same
service modules the REST API uses (`app.services.incidents`, `app.services.retrieval`,
`app.services.knowledge_graph`) — there is one implementation of "search" or "create
incident," never a second copy behind MCP. It:

- Uses the official `mcp` Python SDK (`MCPServer`, v2 line — note the class was
  renamed from `FastMCP` in this SDK generation; do not follow older tutorials that
  reference `FastMCP`).
- Talks **stdio only** — no network listener, no auth surface. Claude Code launches
  it as a subprocess and communicates over its stdin/stdout.
- Lives in a package named **`mcp_server/`, not `mcp/`** — deliberately, to avoid
  colliding with the installed `mcp` PyPI package itself. `mcp/` was the name
  suggested by this project's original spec, but running `python -m mcp.server.main`
  from the repo root resolves `mcp` to the *local* directory (Python's `-m` puts the
  current working directory first on `sys.path`), not the installed SDK, and fails
  with `No module named mcp.server.main`. Verified the hard way — see the module's
  own docstring.
- Reads/writes the **same canonical database** as the FastAPI app (default settings,
  no MCP-specific configuration needed) — nothing MCP-specific is a second source of
  truth.

## Available tools

| Tool | Purpose |
|---|---|
| `search_engineering_knowledge` | Hybrid search (lexical + semantic + relationship expansion) |
| `get_incident` | Full detail for one incident |
| `get_related_incidents` | Knowledge-graph expansion from one incident |
| `search_by_error_code` | Same hybrid pipeline, tuned for exact identifiers |
| `search_by_technology` | Incidents tagged with a given technology |
| `search_by_project` | Incidents tagged with a given project |
| `find_previous_solution` | Like search, but only among `solved` incidents |
| `get_engineering_history` | Recent incidents, optionally filtered by status |
| `create_incident` | Save a new incident (zero-friction: only `raw_problem` required) |
| `update_incident` | Add root cause/solution/lesson/status to an existing incident |

Read tools are annotated `readOnlyHint: true`; write tools (`create_incident`,
`update_incident`) are annotated non-idempotent. Per the MCP spec, these annotations
are advisory only — Claude Code's own consent UI, not this server, is what actually
gates a destructive call.

## Setup

1. Install dependencies (includes the `mcp` package by default):
   ```bash
   uv sync
   ```
2. Register the server with Claude Code, at **user scope** so it's available from any
   project, not just this repo:
   ```bash
   claude mcp add --scope user engineering-memory -- uv run --project /path/to/recall python -m mcp_server.server.main
   ```
   Replace `/path/to/recall` with this repository's absolute path.
3. Verify the connection:
   ```bash
   claude mcp list
   claude mcp get engineering-memory
   ```
   Inside a Claude Code session, `/mcp` shows the live tool list and lets you
   reconnect without restarting.
4. **Restart is required after any registration change** — Claude Code reads its MCP
   config at session start only.
5. Optional steering hint in `~/.claude/CLAUDE.md` (your personal, all-projects memory
   file, loaded into every session regardless of repo):
   > When debugging an error or asked "have I seen this before," check the
   > `engineering-memory` MCP tools before searching the web.

   Keep it short — long CLAUDE.md files reduce adherence. This is a soft nudge, not
   enforcement (see docs/RESEARCH.md's tradeoffs table); tool descriptions are the
   channel confirmed to work reliably, not the MCP `instructions` field.

**Not yet tested against a real `claude` CLI** — this sandboxed dev environment has no
`claude` binary installed, so `claude mcp add`/`claude mcp list` above are the
documented, spec-verified commands but have not been run end-to-end here. What *is*
verified end-to-end (`backend/tests/test_mcp_server.py`): launching the server exactly
as Claude Code would (a subprocess over stdio) and driving it with the real MCP client
SDK — tool listing, `create_incident`, `search_engineering_knowledge`, `get_incident`
all confirmed working.

## Example usage from Claude Code

```
User: "This CUDA error looks familiar. Check my engineering memory."

Claude:
  1. Calls search_engineering_knowledge("CUDA <the specific error text>")
  2. Gets back ranked results with a signals breakdown and a `degraded` flag
  3. If a strong match exists, calls get_incident(id) for full detail
     (root cause, environment, failed attempts, lesson learned)
  4. Compares the recorded environment against the current one before
     reusing the historical solution — a past fix is evidence, not a
     guarantee (Section 72)
```

## Security

- stdio-only: no network listener, no bearer tokens, no CORS surface to secure.
- Every tool input is validated by its Pydantic model before touching the database
  (same validation the REST API applies).
- `create_incident`/`update_incident` go through the identical service-layer code
  path as the REST API — no MCP-specific bypass of validation or business rules.
- The server process reads the same `data/engineering.db` the rest of the app uses;
  file permissions on that path are your only access control (this is a single-user
  local tool, not a multi-tenant service).

## Saving new incidents from Claude Code

After solving a hard problem in a normal Claude Code session, you (or Claude, if
asked) can call `create_incident` directly — only the problem description is
required. Add `root_cause`/`solution`/`lesson_learned` in the same turn via
`update_incident`, or leave them for later (Section 36's "close the knowledge loop").
