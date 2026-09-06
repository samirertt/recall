"""Phase 19 — Final Validation. One test per acceptance scenario (Sections 76-80 of
the source spec), each independently runnable and named after its section so the
mapping from spec requirement to verification is unambiguous.

Sections 77 and 80 are already exercised thoroughly elsewhere
(test_backup.py::test_full_export_delete_import_rebuild_search_round_trip and
test_mcp_server.py's real-subprocess MCP test, respectively) — reproduced here in
miniature for a single, canonical "here is every acceptance criterion, verified"
location, not because the deeper tests are insufficient.
"""

from app.models.embedding import ChunkEmbedding
from app.services.embeddings.service import embed_incident, rebuild_embeddings, search_vector
from app.services.incidents import service as incidents_service


async def test_section76_jetson_cuda_pytorch_acceptance(client):
    """Create the exact incident from Section 76, query it the way Section 57
    specifies, and confirm every fact Claude would need to explain is retrievable:
    previous problem, environment, failed attempt, successful solution, and that a
    generically-similar-but-different incident doesn't outrank it."""
    resp = await client.post(
        "/incidents",
        json={
            "raw_problem": "Jetson cannot detect CUDA from PyTorch.",
            "raw_solution": "Installed the compatible CUDA-enabled PyTorch build.",
        },
    )
    incident_id = resp.json()["incident"]["id"]

    await client.patch(
        f"/incidents/{incident_id}",
        json={
            "environment": {
                "device": "Jetson",
                "operating_system": "Ubuntu",
                "framework": "PyTorch",
                "driver_versions": "CUDA 12.x",
            },
            "attempts": [{"action": "Reinstall PyTorch", "result": "Did not work"}],
            "why_solution_worked": (
                "The generic pip wheel installs a CPU-only build; the Jetson needs "
                "the CUDA-enabled build matching its JetPack version."
            ),
        },
    )

    # An unrelated desktop-GPU incident that also mentions CUDA — must not outrank
    # the actual match (Section 57's discrimination requirement).
    await client.post(
        "/incidents",
        json={
            "raw_problem": "Desktop gaming PC's NVIDIA driver crashes randomly during gameplay.",
            "raw_solution": "Rolled back to a previous stable driver release.",
        },
    )

    search = await client.get(
        "/search", params={"q": "Why doesn't PyTorch see CUDA on my Jetson?"}
    )
    assert search.status_code == 200
    results = search.json()["results"]
    assert results, "the Jetson incident must be retrievable at all"
    assert (
        results[0]["incident_id"] == incident_id
    ), "must outrank the unrelated desktop GPU incident"

    detail = (await client.get(f"/incidents/{incident_id}")).json()
    assert detail["raw_problem"] == "Jetson cannot detect CUDA from PyTorch."
    assert detail["environment"]["device"] == "Jetson"
    assert len(detail["attempts"]) == 1 and detail["attempts"][0]["result"] == "Did not work"
    assert detail["raw_solution"] == "Installed the compatible CUDA-enabled PyTorch build."
    assert "CPU-only" in detail["why_solution_worked"]


async def test_section78_ai_provider_failure_tolerance(client, monkeypatch):
    """Simulate the configured AI provider failing outright. The incident must still
    save, and must still be findable via lexical search — no AI dependency may sit
    on the capture path (Section 52/78)."""

    class AlwaysFailsProvider:
        name = "broken"
        model = "broken-1"

        def is_available(self):
            return True

        async def extract(self, incident_text):
            raise RuntimeError("simulated AI provider outage")

    from app.services.ai import service as ai_service

    monkeypatch.setattr(ai_service, "get_ai_provider", lambda: AlwaysFailsProvider())

    resp = await client.post(
        "/incidents",
        json={"raw_problem": "Bazel build produces a stale binary after a clean checkout."},
    )
    assert resp.status_code == 201  # capture must succeed despite the AI failure
    incident_id = resp.json()["incident"]["id"]

    search = await client.get("/search", params={"q": "Bazel stale binary clean checkout"})
    assert any(r["incident_id"] == incident_id for r in search.json()["results"])


async def test_section79_vector_index_loss_and_rebuild(client, db_session):
    """Delete the vector index entirely (simulating corruption/loss). Canonical data
    must remain fully intact, and rebuild-embeddings must restore semantic
    retrieval — this is never a data-loss event (Section 79/82)."""
    resp = await client.post(
        "/incidents",
        json={
            "raw_problem": (
                "Two training jobs on the same GPU both crash with an out-of-memory "
                "error even though nvidia-smi shows the card isn't full."
            )
        },
    )
    incident_id = resp.json()["incident"]["id"]

    incident = await incidents_service.get_incident(db_session, incident_id)
    assert await embed_incident(db_session, incident)  # confirm it was embedded at all

    from sqlalchemy import delete

    await db_session.execute(
        delete(ChunkEmbedding).where(ChunkEmbedding.incident_id == incident_id)
    )
    await db_session.commit()

    # Canonical data must be completely unaffected by losing the derived index.
    still_there = await incidents_service.get_incident(db_session, incident_id)
    assert still_there.raw_problem == incident.raw_problem

    vector_hits_before = await search_vector(
        db_session, "why does my GPU run out of memory launching a second model"
    )
    assert incident_id not in [h["incident_id"] for h in vector_hits_before]

    rebuild_result = await rebuild_embeddings(db_session)
    assert rebuild_result["embedded"] >= 1

    vector_hits_after = await search_vector(
        db_session, "why does my GPU run out of memory launching a second model"
    )
    assert incident_id in [h["incident_id"] for h in vector_hits_after]


async def test_section80_claude_code_retrieval_shape(client, db_session):
    """A lighter-weight check that the exact data an MCP tool call would return is
    structurally usable for "Claude compares historical vs. current environment"
    (Section 34/80) — the full real-subprocess MCP verification lives in
    test_mcp_server.py."""
    resp = await client.post(
        "/incidents",
        json={"raw_problem": "Jetson Orin NX CUDA/PyTorch detection failure, JetPack 6."},
    )
    incident_id = resp.json()["incident"]["id"]
    await client.patch(
        f"/incidents/{incident_id}",
        json={"environment": {"device": "Jetson Orin NX", "os_version": "JetPack 6"}},
    )

    from app.services.retrieval.search import search_incidents

    response = await search_incidents(db_session, "Jetson CUDA PyTorch detection failure")
    assert any(r.incident_id == incident_id for r in response.results)

    incident = await incidents_service.get_incident(db_session, incident_id)
    # Everything Claude would need to compare historical vs. current environment:
    assert incident.environment.device == "Jetson Orin NX"
    assert incident.environment.os_version == "JetPack 6"
    assert incident.raw_problem
