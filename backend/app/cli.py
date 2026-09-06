"""`engkb` CLI (Phase 13; Section 37/38) — useful independently of the web UI.

Run via `uv run python -m app.cli <command>` or the `scripts/engkb` wrapper.
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from app.core.config import get_settings
from app.db.session import get_session_factory, is_fts5_available
from app.models.enums import IncidentStatus
from app.schemas.incident import IncidentCreate, QuickCaptureIn
from app.services.backup.service import BackupError, export_archive, import_archive
from app.services.embeddings.provider import get_embedding_provider
from app.services.embeddings.service import rebuild_embeddings
from app.services.incidents import service as incidents_service
from app.services.retrieval.lexical import rebuild_fts_index
from app.services.retrieval.search import search_incidents

app = typer.Typer(help="Engineering Memory CLI", no_args_is_help=True)


def _run(coro):
    return asyncio.run(coro)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="What are you looking for?")],
    limit: Annotated[int, typer.Option(help="Max results")] = 10,
):
    """Hybrid search over the knowledge base."""

    async def _go():
        async with get_session_factory()() as session:
            response = await search_incidents(session, query, limit=limit)
            if response.degraded.vector_search:
                typer.echo("(semantic search unavailable — showing lexical results only)")
            if not response.results:
                typer.echo("No results.")
                return
            for r in response.results:
                typer.echo(f"#{r.incident_id}  [{r.status}]  {r.title or '(untitled)'}")
                signals = list(r.signals.model_dump(exclude_none=True))
                typer.echo(f"    relevance {r.fused_score * 100:.0f}%  signals={signals}")

    _run(_go())


@app.command()
def add(
    problem: Annotated[str | None, typer.Option(help="Problem description")] = None,
    solution: Annotated[str | None, typer.Option(help="Solution, if resolved")] = None,
):
    """Create a new incident (Section 18: only the problem is required)."""
    if problem is None:
        problem = typer.prompt("What happened?")
    if solution is None:
        solution = typer.prompt("How did you solve it? (blank if unresolved)", default="")
        solution = solution or None

    async def _go():
        async with get_session_factory()() as session:
            incident = await incidents_service.create_incident(
                session, IncidentCreate(raw_problem=problem, raw_solution=solution)
            )
            typer.echo(f"Created incident #{incident.id}: {incident.title}")

    _run(_go())


@app.command("quick-add")
def quick_add(text: Annotated[str | None, typer.Argument()] = None):
    """Save a raw paste immediately (Section 19). Reads stdin if TEXT is omitted."""
    if text is None:
        text = typer.get_text_stream("stdin").read()
    if not text.strip():
        typer.echo("Nothing to save (empty input).", err=True)
        raise typer.Exit(1)

    async def _go():
        async with get_session_factory()() as session:
            incident = await incidents_service.quick_capture(session, QuickCaptureIn(raw_text=text))
            typer.echo(f"Saved incident #{incident.id}: {incident.title}")

    _run(_go())


@app.command()
def incident(incident_id: int):
    """Show one incident's full detail."""

    async def _go():
        async with get_session_factory()() as session:
            inc = await incidents_service.get_incident(session, incident_id)
            if inc is None:
                typer.echo(f"No incident #{incident_id}", err=True)
                raise typer.Exit(1)
            typer.echo(f"#{inc.id}  [{inc.status}]  {inc.title or '(untitled)'}")
            typer.echo(f"\nProblem:\n{inc.raw_problem}")
            if inc.raw_solution:
                typer.echo(f"\nReported solution:\n{inc.raw_solution}")
            for field, label in (
                ("root_cause", "Root cause"),
                ("solution", "Solution"),
                ("why_solution_worked", "Why it worked"),
                ("lesson_learned", "Lesson learned"),
            ):
                value = getattr(inc, field)
                if value:
                    typer.echo(f"\n{label}:\n{value}")
            if inc.attempts:
                typer.echo("\nFailed attempts:")
                for a in inc.attempts:
                    typer.echo(f"  - {a.action}" + (f" -> {a.result}" if a.result else ""))

    _run(_go())


@app.command("list")
def list_incidents(
    status: Annotated[IncidentStatus | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option()] = 50,
):
    """List recent incidents."""

    async def _go():
        async with get_session_factory()() as session:
            incidents = await incidents_service.list_incidents(session, limit=limit, status=status)
            if not incidents:
                typer.echo("No incidents yet.")
                return
            for inc in incidents:
                review = " [needs review]" if inc.needs_ai_review else ""
                typer.echo(f"#{inc.id}  [{inc.status}]  {inc.title or '(untitled)'}{review}")

    _run(_go())


@app.command()
def export(path: Path):
    """Export the entire knowledge base to a portable zip archive."""
    try:
        manifest = export_archive(path)
    except BackupError as exc:
        typer.echo(f"Export failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Exported {manifest['database']['row_counts']['incidents']} incidents "
               f"and {manifest['attachments']['count']} attachments to {path}")


@app.command(name="import")
def import_(path: Path):
    """Import a previously exported archive. Fails closed on any checksum/schema
    mismatch — never partially applies a bad archive."""
    try:
        report = import_archive(path)
    except BackupError as exc:
        typer.echo(f"Import failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Restored {report.incidents_restored} incidents, "
               f"{report.attachments_restored} attachments.")
    if report.attachments_missing:
        typer.echo(f"  {len(report.attachments_missing)} attachments referenced but "
                    f"not found in the archive.", err=True)
    if report.schema_migrated:
        typer.echo("  Schema was migrated to the current version during import.")
    typer.echo("Run 'engkb rebuild-index' and 'engkb rebuild-embeddings' next — "
               "derived indexes are never included in an archive.")


@app.command("rebuild-index")
def rebuild_index():
    """Rebuild the FTS5 lexical index from canonical data."""

    async def _go():
        async with get_session_factory()() as session:
            await rebuild_fts_index(session)
            typer.echo("FTS5 index rebuilt.")

    _run(_go())


@app.command("rebuild-embeddings")
def rebuild_embeddings_cmd():
    """Re-embed every incident from canonical text."""

    async def _go():
        async with get_session_factory()() as session:
            result = await rebuild_embeddings(session)
            if result["embedded"] == 0 and "reason" in result:
                typer.echo(f"Skipped: {result['reason']}")
            else:
                typer.echo(f"Re-embedded {result['embedded']} incidents using {result['model']}.")

    _run(_go())


@app.command()
def doctor():
    """Check database, FTS5, embeddings, AI, and attachment health."""
    settings = get_settings()
    typer.echo(f"Database path:        {settings.database_path}")
    typer.echo(f"Database exists:      {settings.database_path.exists()}")
    typer.echo(f"FTS5 available:       {is_fts5_available()}")

    provider = get_embedding_provider()
    typer.echo(f"Embedding provider:   {provider.model_name if provider else 'unavailable'}")
    typer.echo(f"AI provider config:   {settings.ai_provider}")

    if settings.database_path.exists():
        conn = sqlite3.connect(str(settings.database_path))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            typer.echo(f"DB integrity check:  {integrity}")
            incident_count = conn.execute("SELECT count(*) FROM incidents").fetchone()[0]
            attachment_count = conn.execute("SELECT count(*) FROM attachments").fetchone()[0]
            typer.echo(f"Incidents:            {incident_count}")
            typer.echo(f"Attachments (DB rows):{attachment_count}")

            missing_files = 0
            for (relative_path,) in conn.execute("SELECT relative_path FROM attachments"):
                if not (settings.attachments_dir / relative_path).exists():
                    missing_files += 1
            if missing_files:
                typer.echo(f"  WARNING: {missing_files} attachment file(s) missing on disk")
        finally:
            conn.close()
    else:
        typer.echo("  (run 'uv run alembic upgrade head' to initialize)")


if __name__ == "__main__":
    app()
