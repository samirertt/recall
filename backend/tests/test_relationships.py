"""Phase 11 tests: relationship authoring, traversal API, and AI-suggested relations
(computed only, never auto-persisted — Section 29)."""


async def _create(client, problem, solution=None):
    payload = {"raw_problem": problem}
    if solution:
        payload["raw_solution"] = solution
    resp = await client.post("/incidents", json=payload)
    return resp.json()["incident"]["id"]


async def test_create_list_and_delete_relation(client):
    a = await _create(client, "Kubernetes pod evicted under memory pressure")
    b = await _create(client, "OOMKilled pod on a different cluster")

    create_resp = await client.post(
        f"/incidents/{a}/relations",
        json={"target_incident_id": b, "relation_type": "related_to", "note": "same symptom"},
    )
    assert create_resp.status_code == 201
    relation = create_resp.json()
    assert relation["source"] == "human"

    list_resp = await client.get(f"/incidents/{a}/relations")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # Symmetric ordering: querying from either side must return the same relation.
    list_from_b = await client.get(f"/incidents/{b}/relations")
    assert len(list_from_b.json()) == 1

    delete_resp = await client.delete(f"/incidents/{a}/relations/{relation['id']}")
    assert delete_resp.status_code == 204
    assert (await client.get(f"/incidents/{a}/relations")).json() == []


async def test_cannot_relate_incident_to_itself(client):
    a = await _create(client, "Some incident")
    resp = await client.post(
        f"/incidents/{a}/relations", json={"target_incident_id": a, "relation_type": "related_to"}
    )
    assert resp.status_code == 400


async def test_create_relation_404s_on_missing_incident(client):
    a = await _create(client, "Some incident")
    resp = await client.post(
        f"/incidents/{a}/relations",
        json={"target_incident_id": 999999, "relation_type": "related_to"},
    )
    assert resp.status_code == 404


async def test_related_incidents_traversal_endpoint(client):
    a = await _create(client, "A")
    b = await _create(client, "B")
    c = await _create(client, "C")
    await client.post(
        f"/incidents/{a}/relations", json={"target_incident_id": b, "relation_type": "related_to"}
    )
    await client.post(
        f"/incidents/{b}/relations", json={"target_incident_id": c, "relation_type": "related_to"}
    )

    one_hop = await client.get(f"/incidents/{a}/related", params={"max_depth": 1})
    ids = {r["incident_id"] for r in one_hop.json()}
    assert ids == {b}

    two_hop = await client.get(f"/incidents/{a}/related", params={"max_depth": 2})
    ids = {r["incident_id"] for r in two_hop.json()}
    assert ids == {b, c}


async def test_suggested_relations_never_auto_persisted(client):
    a = await _create(
        client,
        "Two training jobs on the same GPU both crash with an out-of-memory error.",
    )
    await _create(client, "GPU runs out of memory when a second training job starts.")

    resp = await client.get(f"/incidents/{a}/relations/suggested")
    assert resp.status_code == 200
    # Whether or not a suggestion surfaces depends on embedding similarity, but
    # regardless of that, suggestions must never themselves create DB rows.
    assert (await client.get(f"/incidents/{a}/relations")).json() == []
