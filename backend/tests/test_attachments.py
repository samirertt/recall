"""Phase 6 tests: upload never blocks on extraction failure (Section 52), text/PDF
extraction works, OCR gracefully degrades when Tesseract isn't installed (a genuine
condition in this dev environment, not simulated), and extracted text is searchable
without altering the original file (Section 17)."""

import io

import pytest


async def _create_incident(client) -> int:
    resp = await client.post("/incidents", json={"raw_problem": "placeholder incident"})
    return resp.json()["incident"]["id"]


async def test_upload_text_attachment_extracts_and_is_downloadable(client):
    incident_id = await _create_incident(client)
    content = b"Traceback (most recent call last):\nModuleNotFoundError: No module named 'foo'\n"

    resp = await client.post(
        f"/incidents/{incident_id}/attachments",
        files={"file": ("error.log", io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code == 201
    attachment = resp.json()
    assert attachment["filename"] == "error.log"
    assert attachment["sha256"] == __import__("hashlib").sha256(content).hexdigest()

    download = await client.get(f"/attachments/{attachment['id']}/content")
    assert download.status_code == 200
    assert download.content == content  # original bytes untouched

    extracted = await client.get(f"/attachments/{attachment['id']}/extracted-text")
    assert extracted.status_code == 200
    body = extracted.json()
    assert body["status"] == "success"
    assert "ModuleNotFoundError" in body["text"]


async def test_upload_pdf_attachment_extracts_text_layer(client):
    pymupdf = pytest.importorskip("pymupdf")
    incident_id = await _create_incident(client)

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "CMakeError: could not find compatible toolchain file")
    pdf_bytes = doc.tobytes()

    resp = await client.post(
        f"/incidents/{incident_id}/attachments",
        files={"file": ("report.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 201
    attachment_id = resp.json()["id"]

    extracted = await client.get(f"/attachments/{attachment_id}/extracted-text")
    assert extracted.status_code == 200
    body = extracted.json()
    assert body["status"] == "success"
    assert "CMakeError" in body["text"]
    assert body["extractor_engine"] == "pymupdf"


async def test_upload_image_attachment_degrades_gracefully_without_tesseract(client):
    """This environment genuinely has no Tesseract binary installed — this exercises
    the real degradation path (Section 52), not a simulated one."""
    Image = pytest.importorskip("PIL.Image")
    incident_id = await _create_incident(client)

    buf = io.BytesIO()
    Image.new("RGB", (200, 60), color="white").save(buf, format="PNG")

    resp = await client.post(
        f"/incidents/{incident_id}/attachments",
        files={"file": ("screenshot.png", io.BytesIO(buf.getvalue()), "image/png")},
    )
    # The attachment upload itself must always succeed, regardless of OCR availability.
    assert resp.status_code == 201
    attachment_id = resp.json()["id"]

    extracted = await client.get(f"/attachments/{attachment_id}/extracted-text")
    assert extracted.status_code == 200
    body = extracted.json()
    assert body["status"] == "failed"
    assert body["error"] is not None


async def test_search_finds_incident_by_attachment_text(client):
    incident_id = await _create_incident(client)
    content = b"ECONNREFUSED 127.0.0.1:5432 connecting to postgres pool exhausted"
    await client.post(
        f"/incidents/{incident_id}/attachments",
        files={"file": ("stacktrace.log", io.BytesIO(content), "text/plain")},
    )

    resp = await client.get("/search", params={"q": "ECONNREFUSED postgres pool"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["incident_id"] == incident_id for r in results)


async def test_attachment_storage_rejects_path_traversal_in_relative_path():
    from app.services.attachments.storage import read_attachment_bytes

    with pytest.raises(ValueError):
        read_attachment_bytes("../../../../etc/passwd")


async def test_download_nonexistent_attachment_404s(client):
    resp = await client.get("/attachments/999999/content")
    assert resp.status_code == 404
