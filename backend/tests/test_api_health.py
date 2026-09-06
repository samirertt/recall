async def test_health_reports_fts5_and_config(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["fts5_available"] is True
