"""Search response schema (Phase 7; full hybrid shape anticipated per docs/ARCHITECTURE.md
§ 5 — only the lexical signal is populated until Phase 8/9 land, but the response shape
(`signals`, `degraded`) is the final one so the frontend/MCP contract doesn't change later.
"""

from pydantic import BaseModel

from app.models.enums import IncidentStatus


class SearchSignals(BaseModel):
    lexical: dict | None = None
    trigram: dict | None = None
    vector: dict | None = None
    exact_match: dict | None = None


class SearchDegraded(BaseModel):
    vector_search: bool = True  # Phase 8 not yet implemented — always degraded for now


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
