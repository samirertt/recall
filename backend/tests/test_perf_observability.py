"""Phase 17: duration logging exists and never leaks content (Section 54)."""

import logging


async def test_search_and_embed_log_duration_without_content(client, caplog):
    caplog.set_level(logging.DEBUG, logger="app.perf")

    secret_problem = "TOTALLY-UNIQUE-SECRET-INCIDENT-TEXT-MARKER-98213"
    await client.post("/incidents", json={"raw_problem": secret_problem})
    await client.get("/search", params={"q": "unique secret marker"})

    perf_records = [r for r in caplog.records if r.name == "app.perf"]
    assert any("search" in r.message for r in perf_records)
    assert any("embed_incident" in r.message for r in perf_records)
    for record in perf_records:
        assert secret_problem not in record.message
        assert "unique secret marker" not in record.message
