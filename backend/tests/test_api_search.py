"""Phase 7 lexical search API tests — including the acceptance-test shape from
Section 76/57: an environment-specific match should outrank a generic one, and
retrieval must survive a query containing FTS5-special characters unscathed."""


async def test_search_finds_created_incident_by_keyword(client):
    await client.post(
        "/incidents",
        json={
            "raw_problem": "Jetson Orin NX stopped detecting CUDA after reinstalling "
            "Python packages; PyTorch build was CPU-only.",
            "raw_solution": "Installed the CUDA-enabled PyTorch build for the Jetson.",
        },
    )

    resp = await client.get("/search", params={"q": "CUDA PyTorch"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"]["vector_search"] is True  # Phase 8 not implemented yet
    assert len(body["results"]) >= 1
    assert "CUDA" in body["results"][0]["title"] or "Jetson" in body["results"][0]["title"]


async def test_search_survives_special_characters(client):
    resp = await client.get("/search", params={"q": '"weird: query* (test) AND OR NOT'})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_search_no_results_returns_empty_not_error(client):
    resp = await client.get("/search", params={"q": "zzz_nonexistent_term_zzz"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_search_ranks_exact_title_match_above_incidental_mention(client):
    await client.post(
        "/incidents",
        json={
            "raw_problem": "Docker networking issue causes DNS resolution to fail "
            "inside containers."
        },
    )
    await client.post(
        "/incidents",
        json={
            "raw_problem": "General build flakiness",
            "raw_solution": "Unrelated note: once saw a Docker container too.",
        },
    )

    resp = await client.get("/search", params={"q": "Docker networking DNS"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1
    assert "Docker networking" in results[0]["title"]
