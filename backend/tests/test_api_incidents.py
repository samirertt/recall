"""Phase 3/5 API tests: zero-friction capture, quick capture, CRUD, archive-not-delete."""


async def test_zero_friction_create_requires_only_raw_problem(client):
    resp = await client.post("/incidents", json={"raw_problem": "Jetson cannot detect CUDA."})
    assert resp.status_code == 201
    body = resp.json()
    assert body["incident"]["raw_problem"] == "Jetson cannot detect CUDA."
    assert body["incident"]["status"] == "unresolved"
    assert body["incident"]["title"] == "Jetson cannot detect CUDA."
    assert body["incident"]["needs_ai_review"] is True
    assert body["possible_duplicates"] == []


async def test_create_with_solution_marks_solved(client):
    resp = await client.post(
        "/incidents",
        json={"raw_problem": "Build fails.", "raw_solution": "Cleared stale cache."},
    )
    assert resp.status_code == 201
    assert resp.json()["incident"]["status"] == "solved"


async def test_quick_capture_saves_immediately(client):
    resp = await client.post(
        "/incidents/quick-capture",
        json={
            "raw_text": "ROS2 topic silently not delivered due to QoS mismatch, "
            "fixed by aligning reliability policy."
        },
    )
    assert resp.status_code == 201
    assert resp.json()["incident"]["raw_problem"].startswith("ROS2 topic")


async def test_get_list_update_and_archive_incident(client):
    create_resp = await client.post(
        "/incidents", json={"raw_problem": "CMake picks wrong compiler."}
    )
    incident_id = create_resp.json()["incident"]["id"]

    get_resp = await client.get(f"/incidents/{incident_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == incident_id

    list_resp = await client.get("/incidents")
    assert list_resp.status_code == 200
    assert any(item["id"] == incident_id for item in list_resp.json())

    update_resp = await client.patch(
        f"/incidents/{incident_id}",
        json={
            "root_cause": "Wrong toolchain file picked up by CMakeLists.",
            "solution": "Set CMAKE_TOOLCHAIN_FILE explicitly.",
            "status": "solved",
            "environment": {"operating_system": "Ubuntu", "os_version": "24.04"},
            "attempts": [{"action": "Delete build dir", "result": "Same error"}],
            "technology_names": ["CMake", "C++"],
            "tag_names": ["build-system"],
        },
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["status"] == "solved"
    assert updated["root_cause"].startswith("Wrong toolchain")
    assert updated["environment"]["os_version"] == "24.04"
    assert len(updated["attempts"]) == 1

    archive_resp = await client.post(f"/incidents/{incident_id}/archive")
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "obsolete"

    # Archiving is Section 43's "never destroy knowledge" — the row must still exist.
    still_there = await client.get(f"/incidents/{incident_id}")
    assert still_there.status_code == 200


async def test_get_nonexistent_incident_404s(client):
    resp = await client.get("/incidents/999999")
    assert resp.status_code == 404


async def test_create_rejects_absurdly_long_raw_problem(client):
    """Phase 16 security pass: an unbounded paste could otherwise force an
    unbounded embedding/FTS-write cost per request."""
    resp = await client.post("/incidents", json={"raw_problem": "x" * 500_001})
    assert resp.status_code == 422
