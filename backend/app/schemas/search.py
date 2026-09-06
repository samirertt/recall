"""Search response schema (Phase 7/9; docs/ARCHITECTURE.md § 5).

Every result carries a per-signal breakdown and the fusion `weights_profile` actually
used, so "why did this rank highly" is answerable from the response payload alone
(Section 70), and stays accurate even after weights are later edited (docs/RESEARCH.md
§ Hybrid Retrieval).
"""

from pydantic import BaseModel

from app.models.enums import IncidentStatus


class SearchSignals(BaseModel):
    lexical: dict | None = None
    trigram: dict | None = None
    vector: dict | None = None
    attachment: dict | None = None
    relationship: dict | None = None
    exact_match: dict | None = None


class SearchDegraded(BaseModel):
    vector_search: bool = True


class SearchResult(BaseModel):
    incident_id: int
    title: str | None
    status: IncidentStatus
    fused_score: float
    signals: SearchSignals
    snippet: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    degraded: SearchDegraded
    weights_profile: str
