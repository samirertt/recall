"""Phase 10 tests: AI provider abstraction, heuristic fallback, provenance/
verification, and the "never overwrite an already-set field" rule (Section 21)."""


from app.models.enums import ProvenanceBasis
from app.schemas.extraction import ExtractionResult, FieldProvenance, IncidentExtraction
from app.services.ai.provider import ClaudeProvider, HeuristicProvider, get_ai_provider
from app.services.ai.service import enrich_incident
from app.services.ai.verification import verify_field, verify_list_field
from app.services.incidents.service import get_incident


async def test_heuristic_provider_always_available_and_titles_from_first_line():
    provider = HeuristicProvider()
    assert provider.is_available() is True
    result = await provider.extract("CMake picks the wrong compiler.\nMore detail here.")
    assert result.fields.title == "CMake picks the wrong compiler."
    assert result.provider == "heuristic"


def test_get_ai_provider_falls_back_to_heuristic_without_api_key(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        get_settings(), "ai_provider", "claude"
    )  # configured for Claude, but no key anywhere
    provider = get_ai_provider()
    assert provider.name == "heuristic"


def test_get_ai_provider_selects_claude_when_key_present(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_provider", "claude")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key-for-selection-test")
    provider = get_ai_provider()
    assert isinstance(provider, ClaudeProvider)
    assert provider.is_available() is True


def test_verify_field_explicit_when_quoted_verbatim():
    provenance = verify_field("root_cause", "stale build cache", "raw text: stale build cache here")
    assert provenance.basis == ProvenanceBasis.explicit
    assert provenance.confidence == 1.0


def test_verify_field_synthesized_when_not_present_in_source():
    provenance = verify_field(
        "root_cause", "a completely different unrelated made-up sentence", "raw text: foo bar"
    )
    assert provenance.basis == ProvenanceBasis.synthesized


def test_verify_field_unknown_when_empty():
    provenance = verify_field("root_cause", None, "raw text")
    assert provenance.basis == ProvenanceBasis.unknown


def test_verify_list_field_explicit_vs_inferred():
    results = verify_list_field(
        "technologies", ["CUDA", "some-invented-tech"], "issue with CUDA driver"
    )
    by_value = {r.value: r for r in results}
    assert by_value["CUDA"].basis == ProvenanceBasis.explicit
    assert by_value["some-invented-tech"].basis == ProvenanceBasis.inferred


async def test_enrich_incident_never_overwrites_already_set_field(client, db_session):
    resp = await client.post(
        "/incidents",
        json={"raw_problem": "Docker build fails intermittently."},
    )
    incident_id = resp.json()["incident"]["id"]

    # Set up state directly on the session rather than via PATCH /incidents/{id}:
    # that endpoint also triggers its own best-effort re-enrichment (by design), which
    # would immediately re-fill title via the real heuristic provider and defeat this
    # test's setup before FakeProvider ever runs.
    incident = await get_incident(db_session, incident_id)
    incident.root_cause = "Human-verified cause."
    incident.title = None
    await db_session.commit()

    class FakeProvider:
        name = "fake"
        model = "fake-1"

        def is_available(self):
            return True

        async def extract(self, incident_text):
            return ExtractionResult(
                fields=IncidentExtraction(
                    title="AI title", root_cause="AI would have said this instead"
                ),
                provenance={
                    "title": FieldProvenance(
                        value="AI title", basis=ProvenanceBasis.synthesized, confidence=0.6
                    )
                },
                provider=self.name,
                model=self.model,
                prompt_version="v1",
                schema_version="v1",
            )

    await enrich_incident(db_session, incident, provider=FakeProvider())

    refreshed = await get_incident(db_session, incident_id)
    assert refreshed.root_cause == "Human-verified cause."  # not clobbered
    assert refreshed.title == "AI title"  # was null, so this is legitimately filled
    assert refreshed.needs_ai_review is False  # a non-heuristic provider ran


async def test_enrich_incident_writes_provenance_rows(client, db_session):
    resp = await client.post("/incidents", json={"raw_problem": "Segfault in vector code."})
    incident_id = resp.json()["incident"]["id"]

    incident = await get_incident(db_session, incident_id)
    await enrich_incident(db_session, incident)  # default: heuristic in this test env

    from sqlalchemy import select

    from app.models.provenance import ExtractionProvenance

    rows = (
        await db_session.execute(
            select(ExtractionProvenance).where(ExtractionProvenance.incident_id == incident_id)
        )
    ).scalars().all()
    assert any(row.field_name == "title" for row in rows)
