"""Phase 13 tests: the `engkb` CLI, exercised via Typer's CliRunner — genuinely
invoking the command functions (which each do their own asyncio.run), not just
calling the underlying service functions directly."""

from pathlib import Path

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_add_search_list_incident_round_trip(configured_db: Path):
    add_result = runner.invoke(
        app, ["add", "--problem", "Docker DNS resolution fails", "--solution", ""]
    )
    assert add_result.exit_code == 0
    assert "Created incident #1" in add_result.output

    list_result = runner.invoke(app, ["list"])
    assert list_result.exit_code == 0
    assert "Docker DNS resolution fails" in list_result.output

    search_result = runner.invoke(app, ["search", "Docker DNS"])
    assert search_result.exit_code == 0
    assert "#1" in search_result.output

    incident_result = runner.invoke(app, ["incident", "1"])
    assert incident_result.exit_code == 0
    assert "Docker DNS resolution fails" in incident_result.output


def test_quick_add_reads_stdin(configured_db: Path):
    result = runner.invoke(app, ["quick-add"], input="Pasted incident text here.\n")
    assert result.exit_code == 0
    assert "Saved incident #1" in result.output


def test_quick_add_rejects_empty_input(configured_db: Path):
    result = runner.invoke(app, ["quick-add"], input="   \n")
    assert result.exit_code == 1


def test_incident_not_found_exits_nonzero(configured_db: Path):
    result = runner.invoke(app, ["incident", "999"])
    assert result.exit_code == 1


def test_doctor_reports_health(configured_db: Path):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "FTS5 available:       True" in result.output
    assert "DB integrity check:  ok" in result.output


def test_rebuild_index_and_embeddings(configured_db: Path):
    runner.invoke(app, ["add", "--problem", "Segfault in vector code", "--solution", ""])
    index_result = runner.invoke(app, ["rebuild-index"])
    assert index_result.exit_code == 0
    embed_result = runner.invoke(app, ["rebuild-embeddings"])
    assert embed_result.exit_code == 0
    assert "Re-embedded 1 incidents" in embed_result.output


def test_export_then_import_round_trip(configured_db: Path, tmp_path):
    runner.invoke(app, ["add", "--problem", "CLI export/import test", "--solution", ""])
    archive_path = tmp_path / "cli-export.zip"

    export_result = runner.invoke(app, ["export", str(archive_path)])
    assert export_result.exit_code == 0
    assert archive_path.exists()

    import_result = runner.invoke(app, ["import", str(archive_path)])
    assert import_result.exit_code == 0
    assert "Restored 1 incidents" in import_result.output


def test_import_reports_failure_for_bad_archive(configured_db: Path, tmp_path):
    bad_path = tmp_path / "not-a-zip.zip"
    bad_path.write_bytes(b"not actually a zip file")

    result = runner.invoke(app, ["import", str(bad_path)])
    assert result.exit_code == 1
    assert "Import failed" in result.output
