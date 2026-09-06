"""Phase 8/9 tests: real semantic search, graceful degradation without an embedding
provider, and relationship-expansion recall (docs/ARCHITECTURE.md § 5)."""

from app.services.embeddings.service import rebuild_embeddings
from app.services.retrieval import search as search_module
from app.services.retrieval.search import search_incidents


def async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


async def test_semantic_search_finds_conceptually_similar_incident(client, db_session):
    """The classic embeddings test: near-zero lexical overlap, real semantic overlap."""
    await client.post(
        "/incidents",
        json={
            "raw_problem": (
                "Two training jobs on the same GPU both crash with an out-of-memory "
                "error even though nvidia-smi shows the card isn't full."
            ),
            "raw_solution": "Enabled MPS so processes stop fighting over the whole device.",
        },
    )

    # A query using almost entirely different words for the same underlying concept.
    resp = await client.get(
        "/search",
        params={"q": "why does my GPU run out of memory when I launch a second model"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"]["vector_search"] is False
    assert len(body["results"]) >= 1
    assert any(r["signals"].get("vector") for r in body["results"])


async def test_search_degrades_gracefully_without_embedding_provider(client, monkeypatch):
    # Patch the name as *imported into search.py* — patching the origin module
    # (app.services.embeddings.provider) wouldn't affect search.py's already-bound
    # `from ... import get_embedding_provider` reference.
    monkeypatch.setattr(search_module, "get_embedding_provider", lambda: None)

    await client.post("/incidents", json={"raw_problem": "CMake picks the wrong compiler."})
    resp = await client.get("/search", params={"q": "CMake compiler"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"]["vector_search"] is True
    assert len(body["results"]) >= 1  # lexical still works — the whole point of hybrid design
    assert all(r["signals"].get("vector") is None for r in body["results"])


async def test_rebuild_embeddings_reembeds_all_incidents(client, db_session):
    for problem in ["Docker DNS issue", "Git detached HEAD confusion", "Segfault in vector code"]:
        await client.post("/incidents", json={"raw_problem": problem})

    result = await rebuild_embeddings(db_session)
    assert result["embedded"] == 3
    assert result["model"] == "BAAI/bge-small-en-v1.5"


async def test_relationship_expansion_surfaces_related_incident(client, db_session, monkeypatch):
    from app.models.enums import IncidentRelationType
    from app.models.relationships import IncidentRelation

    first = (
        await client.post(
            "/incidents", json={"raw_problem": "Kubernetes pod evicted under memory pressure"}
        )
    ).json()["incident"]
    second = (
        await client.post(
            "/incidents",
            json={
                "raw_problem": "Totally unrelated wording about a completely different topic zzqx"
            },
        )
    ).json()["incident"]

    db_session.add(
        IncidentRelation(
            source_incident_id=first["id"],
            target_incident_id=second["id"],
            relation_type=IncidentRelationType.related_to,
        )
    )
    await db_session.commit()

    # Isolate the relationship-expansion mechanism from real embedding fuzziness:
    # a short "unrelated" filler sentence can still score non-trivially on cosine
    # similarity, which would make this test pass for the wrong reason (found via
    # vector, not via the relationship edge). Force every other signal empty so the
    # second incident can *only* appear through 1-hop expansion from the first.
    monkeypatch.setattr(search_module, "search_lexical", async_return([]))
    monkeypatch.setattr(search_module, "search_trigram", async_return([]))
    monkeypatch.setattr(search_module, "search_attachment_text", async_return([]))
    monkeypatch.setattr(
        search_module,
        "search_vector",
        async_return([{"incident_id": first["id"], "cosine_score": 0.9}]),
    )

    response = await search_incidents(db_session, "Kubernetes pod evicted memory")
    ids = [r.incident_id for r in response.results]
    assert first["id"] in ids
    assert second["id"] in ids
    second_result = next(r for r in response.results if r.incident_id == second["id"])
    assert second_result.signals.relationship is not None
