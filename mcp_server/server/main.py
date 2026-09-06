"""Engineering Memory MCP server (Phase 12; docs/RESEARCH.md § MCP Integration).

Official `mcp` Python SDK (v2 line), stdio transport, ~consolidated tool set matching
Section 33 of the source spec. Every tool delegates to the same backend service
modules the REST API uses (docs/ARCHITECTURE.md § 2) — there is exactly one
implementation of "search" or "create incident," never a second copy behind MCP.

Package is named `mcp_server/`, not `mcp/` (the master spec's Section 60 suggested
name) — `mcp/` collides with the installed `mcp` PyPI package itself. Confirmed the
hard way: `python -m mcp.server.main` run from the repo root resolves `mcp` to the
*local* directory (cwd is sys.path[0] under `-m`), not the installed SDK, and fails
with "No module named mcp.server.main". Renaming the local package sidesteps this
entirely rather than fighting import precedence.

Run directly: `uv run python -m mcp_server.server.main` (from the repo root). Register
with Claude Code: `claude mcp add --scope user engineering-memory -- uv run --project
<repo> python -m mcp_server.server.main` (docs/RESEARCH.md § Claude Code Integration).
"""

import sys
from pathlib import Path
from typing import Annotated

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from pydantic import Field  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.models.enums import IncidentStatus  # noqa: E402
from app.models.incident import Incident  # noqa: E402
from app.models.reference import Project, Technology  # noqa: E402
from app.models.relationships import IncidentProject, IncidentTechnology  # noqa: E402
from app.schemas.incident import (  # noqa: E402
    IncidentCreate,
    IncidentCreateResponse,
    IncidentListItem,
    IncidentRead,
    IncidentUpdate,
)
from app.schemas.relationship import RelatedIncident  # noqa: E402
from app.schemas.search import SearchResponse  # noqa: E402
from app.services.incidents import service as incidents_service  # noqa: E402
from app.services.knowledge_graph import service as kg_service  # noqa: E402
from app.services.retrieval.search import search_incidents  # noqa: E402

server = MCPServer(
    "engineering-memory",
    instructions=(
        "Search and record engineering incident knowledge: past problems, what was "
        "tried, what worked, and why. Check this before deep-debugging something that "
        "may have been solved before. Historical solutions are evidence, not a "
        "guarantee the same fix applies now — compare the recorded environment "
        "against the current one."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, open_world_hint=False, idempotent_hint=False)


def _session():
    return get_session_factory()()


@server.tool(annotations=READ_ONLY)
async def search_engineering_knowledge(
    query: Annotated[str, Field(description="Natural-language description of the problem")],
    limit: Annotated[int, Field(description="Max results", ge=1, le=50)] = 10,
) -> SearchResponse:
    """Hybrid search (lexical + semantic + relationship expansion) over the
    engineering incident knowledge base. Always returns results even if semantic
    search is degraded — check `degraded.vector_search` in the response, and treat a
    historical match as evidence to weigh, not a solution to blindly reapply."""
    async with _session() as session:
        return await search_incidents(session, query, limit=limit)


@server.tool(annotations=READ_ONLY)
async def get_incident(
    incident_id: Annotated[int, Field(description="Incident ID")],
) -> IncidentRead:
    """Fetch one incident's full detail: problem, solution, root cause, environment,
    failed attempts, and lessons learned."""
    async with _session() as session:
        incident = await incidents_service.get_incident(session, incident_id)
        if incident is None:
            raise ValueError(f"No incident with id {incident_id}")
        return incident


@server.tool(annotations=READ_ONLY)
async def get_related_incidents(
    incident_id: Annotated[int, Field(description="Incident ID to expand from")],
    max_depth: Annotated[int, Field(ge=1, le=3)] = 2,
) -> list[RelatedIncident]:
    """Incidents linked to this one via the knowledge graph (human-authored or
    accepted AI-suggested relationships), up to `max_depth` hops away."""
    async with _session() as session:
        related = await kg_service.get_related_incidents(session, incident_id, max_depth)
        return [RelatedIncident(**r) for r in related]


@server.tool(annotations=READ_ONLY)
async def search_by_error_code(
    error_code: Annotated[
        str, Field(description="An exact error code, exception name, or identifier")
    ],
    limit: int = 10,
) -> SearchResponse:
    """Search for an exact error code/identifier. Backed by the same hybrid pipeline
    as `search_engineering_knowledge` — its trigram signal is specifically tuned for
    exact substring matches like error codes and partial log lines."""
    async with _session() as session:
        return await search_incidents(session, error_code, limit=limit)


@server.tool(annotations=READ_ONLY)
async def search_by_technology(
    technology_name: Annotated[
        str, Field(description="Technology/tool/framework name, e.g. 'CUDA' or 'Docker'")
    ],
    limit: int = 20,
) -> list[IncidentListItem]:
    """All incidents associated with a given technology (case-insensitive)."""
    async with _session() as session:
        result = await session.execute(
            select(Incident)
            .join(IncidentTechnology, IncidentTechnology.incident_id == Incident.id)
            .join(Technology, Technology.id == IncidentTechnology.technology_id)
            .where(Technology.name.ilike(technology_name))
            .order_by(Incident.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


@server.tool(annotations=READ_ONLY)
async def search_by_project(
    project_name: Annotated[str, Field(description="Project name (case-insensitive)")],
    limit: int = 20,
) -> list[IncidentListItem]:
    """All incidents associated with a given project."""
    async with _session() as session:
        result = await session.execute(
            select(Incident)
            .join(IncidentProject, IncidentProject.incident_id == Incident.id)
            .join(Project, Project.id == IncidentProject.project_id)
            .where(Project.name.ilike(project_name))
            .order_by(Incident.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


@server.tool(annotations=READ_ONLY)
async def find_previous_solution(
    problem_description: Annotated[str, Field(description="What you're currently debugging")],
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> SearchResponse:
    """Like `search_engineering_knowledge`, but only among incidents already marked
    `solved` — use this specifically when you want a past *fix*, not just a report."""
    async with _session() as session:
        response = await search_incidents(session, problem_description, limit=limit * 3)
        incidents = {
            incident.id: incident
            for incident in (
                await session.execute(
                    select(Incident).where(
                        Incident.id.in_([r.incident_id for r in response.results])
                    )
                )
            )
            .scalars()
            .all()
        }
        response.results = [
            r
            for r in response.results
            if incidents.get(r.incident_id)
            and incidents[r.incident_id].status == IncidentStatus.solved
        ][:limit]
        return response


@server.tool(annotations=READ_ONLY)
async def get_engineering_history(
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    status: IncidentStatus | None = None,
) -> list[IncidentListItem]:
    """Recent incidents, most recent first — a general-purpose browse/timeline tool
    (Section 46), optionally filtered by status."""
    async with _session() as session:
        return await incidents_service.list_incidents(session, limit=limit, status=status)


@server.tool(annotations=WRITE)
async def create_incident(
    raw_problem: Annotated[str, Field(description="What happened, in your own words")],
    raw_solution: Annotated[str | None, Field(description="How it was resolved, if it was")] = None,
) -> IncidentCreateResponse:
    """Save a new incident. Only `raw_problem` is required (Section 18) — this text is
    never rewritten; everything else is filled in by best-effort AI enrichment
    afterward and can be edited later via `update_incident`."""
    async with _session() as session:
        return IncidentCreateResponse(
            incident=await incidents_service.create_incident(
                session, IncidentCreate(raw_problem=raw_problem, raw_solution=raw_solution)
            ),
            possible_duplicates=[],
        )


@server.tool(annotations=WRITE)
async def update_incident(
    incident_id: Annotated[int, Field(description="Incident ID to update")],
    root_cause: str | None = None,
    solution: str | None = None,
    lesson_learned: str | None = None,
    status: IncidentStatus | None = None,
) -> IncidentRead:
    """Add structure to an existing incident. Only the fields you pass are changed;
    everything else is left as-is (never silently cleared)."""
    async with _session() as session:
        provided = {
            k: v
            for k, v in {
                "root_cause": root_cause,
                "solution": solution,
                "lesson_learned": lesson_learned,
                "status": status,
            }.items()
            if v is not None
        }
        # Only kwargs actually passed here end up in `model_fields_set`, so
        # IncidentUpdate's own exclude_unset=True machinery (shared with the REST
        # API's PATCH handler) still only touches these fields — everything else on
        # the incident is left exactly as it was.
        incident = await incidents_service.update_incident(
            session, incident_id, IncidentUpdate(**provided)
        )
        if incident is None:
            raise ValueError(f"No incident with id {incident_id}")
        return incident


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
