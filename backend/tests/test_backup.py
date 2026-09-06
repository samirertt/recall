"""Phase 14 tests: export/import round-trip, checksum verification (fail-closed),
and the Section 77 acceptance scenario (export -> delete local DB -> import ->
rebuild -> search still finds the original incident)."""

import io
import zipfile

import pytest

from app.core.config import get_settings
from app.db.session import reset_engine_cache
from app.services.backup.service import BackupError, export_archive, import_archive
from app.services.embeddings.service import rebuild_embeddings


async def test_export_produces_valid_manifest_and_archive(client, db_session, tmp_path):
    await client.post("/incidents", json={"raw_problem": "Export test incident."})
    dest = tmp_path / "export.zip"

    manifest = export_archive(dest)

    assert dest.exists()
    assert manifest["manifest_version"] == "1.0"
    assert manifest["database"]["row_counts"]["incidents"] == 1
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "core.db" in names
        assert "attachments.sha256.txt" in names


async def test_export_includes_attachments_content_addressed(client, db_session, tmp_path):
    resp = await client.post("/incidents", json={"raw_problem": "Has an attachment."})
    incident_id = resp.json()["incident"]["id"]
    content = b"log line one\nlog line two\n"
    await client.post(
        f"/incidents/{incident_id}/attachments",
        files={"file": ("notes.log", io.BytesIO(content), "text/plain")},
    )

    dest = tmp_path / "export.zip"
    manifest = export_archive(dest)

    assert manifest["attachments"]["count"] == 1
    with zipfile.ZipFile(dest) as zf:
        attachment_files = [
            n for n in zf.namelist() if n.startswith("attachments/") and n.endswith(".log")
        ]
        assert len(attachment_files) == 1
        assert zf.read(attachment_files[0]) == content


async def test_import_rejects_archive_with_tampered_checksum(client, db_session, tmp_path):
    await client.post("/incidents", json={"raw_problem": "Will be tampered with."})
    dest = tmp_path / "export.zip"
    export_archive(dest)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(dest) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "core.db":
                data = data + b"\x00"  # corrupt it without touching the recorded checksum
            dst.writestr(item, data)

    with pytest.raises(BackupError, match="checksum mismatch"):
        import_archive(tampered)


async def test_import_rejects_future_manifest_version(client, db_session, tmp_path):
    await client.post("/incidents", json={"raw_problem": "x"})
    dest = tmp_path / "export.zip"
    export_archive(dest)

    bumped = tmp_path / "bumped.zip"
    with zipfile.ZipFile(dest) as src, zipfile.ZipFile(bumped, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "manifest.json":
                data = data.replace(b'"1.0"', b'"99.0"')
            dst.writestr(item, data)

    with pytest.raises(BackupError, match="manifest_version"):
        import_archive(bumped)


async def test_full_export_delete_import_rebuild_search_round_trip(client, tmp_path):
    """Section 77 acceptance test. Deliberately does not depend on the `db_session`
    fixture: this test disposes the engine mid-test (simulating "delete the local
    copy"), which would leave a `db_session`-held connection dangling at teardown."""
    create_resp = await client.post(
        "/incidents",
        json={
            "raw_problem": "Jetson cannot detect CUDA from PyTorch after reinstall.",
            "raw_solution": "Installed the CUDA-enabled PyTorch build.",
        },
    )
    incident_id = create_resp.json()["incident"]["id"]
    content = b"CUDA error 35: CUDA driver version is insufficient"
    await client.post(
        f"/incidents/{incident_id}/attachments",
        files={"file": ("cuda_error.log", io.BytesIO(content), "text/plain")},
    )

    archive_path = tmp_path / "backup.zip"
    export_archive(archive_path)

    # "Delete local copy": remove the live DB and attachments entirely.
    settings = get_settings()
    db_path = settings.database_path
    attachments_dir = settings.attachments_dir
    await reset_engine_cache()
    db_path.unlink()
    for f in db_path.parent.glob(db_path.name + "*"):
        f.unlink()
    import shutil

    shutil.rmtree(attachments_dir, ignore_errors=True)

    report = import_archive(archive_path)
    assert report.incidents_restored == 1
    assert report.attachments_restored == 1
    assert report.attachments_missing == []

    # Rebuild derived indexes (excluded from the archive by design).
    from app.db.session import get_session_factory

    async with get_session_factory()() as fresh_session:
        rebuild_result = await rebuild_embeddings(fresh_session)
        assert rebuild_result["embedded"] == 1

        from app.services.retrieval.search import search_incidents

        search_response = await search_incidents(fresh_session, "Jetson CUDA PyTorch")
        assert any(r.incident_id == incident_id for r in search_response.results)

        attachment_search = await search_incidents(
            fresh_session, "CUDA driver version insufficient"
        )
        assert any(r.incident_id == incident_id for r in attachment_search.results)
