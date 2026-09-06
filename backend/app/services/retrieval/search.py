"""Builds the API/MCP-facing SearchResponse (Phase 9; docs/ARCHITECTURE.md § 5):
parallel candidate generation (lexical, trigram, vector, attachment text), 1-hop
relationship expansion from the top seeds, weighted-linear fusion with automatic
renormalization when the vector signal is degraded, and a per-result signal
breakdown for explainability (Section 70).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timing import log_duration
from app.models.incident import Incident
from app.schemas.search import SearchDegraded, SearchResponse, SearchResult, SearchSignals
from app.services.embeddings.provider import get_embedding_provider
from app.services.embeddings.service import search_vector
from app.services.knowledge_graph.traversal import related_incident_ids
from app.services.retrieval.fusion import (
    MAX_RELATIONSHIP_ADDITIONS,
    MAX_RELATIONSHIP_SEEDS,
    RELATIONSHIP_SCORE,
    WEIGHTS_PROFILE,
    active_weights,
    fuse,
)
from app.services.retrieval.lexical import search_attachment_text, search_lexical, search_trigram


def _rank_normalize(hits: list[dict], key: str = "incident_id") -> dict[int, float]:
    """Reciprocal-rank falloff — simple, monotonic, bounded in (0, 1]. A documented
    variant of the min-max normalization in docs/RESEARCH.md, not the literal scheme
    (see fusion.py's module docstring for why)."""
    return {hit[key]: round(1.0 / (i + 1), 4) for i, hit in enumerate(hits)}


async def search_incidents(session: AsyncSession, query: str, limit: int = 20) -> SearchResponse:
    """Section 54: durations are logged (never the query text itself)."""
    with log_duration("search", query_len=len(query), limit=limit):
        return await _search_incidents_impl(session, query, limit)


async def _search_incidents_impl(
    session: AsyncSession, query: str, limit: int
) -> SearchResponse:
    lexical_hits = await search_lexical(session, query, limit=limit)
    trigram_ids = await search_trigram(session, query, limit=limit)
    attachment_hits = await search_attachment_text(session, query, limit=limit)

    vector_available = get_embedding_provider() is not None
    vector_hits = await search_vector(session, query, limit=limit) if vector_available else []

    lexical_norm = _rank_normalize(lexical_hits)
    trigram_norm = {incident_id: 1.0 for incident_id in trigram_ids}  # substring hit: binary
    attachment_norm = {hit["incident_id"]: 1.0 for hit in attachment_hits}
    vector_norm = {hit["incident_id"]: max(0.0, hit["cosine_score"]) for hit in vector_hits}
    snippets = {hit["incident_id"]: hit["snippet"] for hit in lexical_hits}
    for hit in attachment_hits:
        snippets.setdefault(hit["incident_id"], hit["snippet"])

    seed_ids = list(
        dict.fromkeys(  # de-duplicated, insertion-ordered union of the strongest signals
            list(lexical_norm) + list(vector_norm) + list(trigram_norm) + list(attachment_norm)
        )
    )

    relationship_norm: dict[int, float] = {}
    for seed_id in seed_ids[:MAX_RELATIONSHIP_SEEDS]:
        if len(relationship_norm) >= MAX_RELATIONSHIP_ADDITIONS:
            break
        for related_id in await related_incident_ids(session, seed_id, max_depth=1):
            if related_id in seed_ids or related_id in relationship_norm:
                continue
            relationship_norm[related_id] = RELATIONSHIP_SCORE
            if len(relationship_norm) >= MAX_RELATIONSHIP_ADDITIONS:
                break

    all_ids = seed_ids + list(relationship_norm)
    if not all_ids:
        return SearchResponse(
            query=query,
            results=[],
            degraded=SearchDegraded(vector_search=not vector_available),
            weights_profile=WEIGHTS_PROFILE,
        )

    incidents = {
        incident.id: incident
        for incident in (
            await session.execute(select(Incident).where(Incident.id.in_(all_ids)))
        )
        .scalars()
        .all()
    }

    weights = active_weights(vector_available)
    results: list[SearchResult] = []
    for incident_id in all_ids:
        incident = incidents.get(incident_id)
        if incident is None:  # stale index row racing a delete — skip rather than 500
            continue

        signal_scores: dict[str, float] = {}
        signals = SearchSignals()
        if incident_id in lexical_norm:
            signal_scores["lexical"] = lexical_norm[incident_id]
            signals.lexical = {"normalized": lexical_norm[incident_id]}
        if incident_id in trigram_norm:
            signal_scores["trigram"] = trigram_norm[incident_id]
            signals.trigram = {"hit": True}
        if incident_id in vector_norm:
            signal_scores["vector"] = vector_norm[incident_id]
            signals.vector = {"cosine": round(vector_norm[incident_id], 4)}
        if incident_id in attachment_norm:
            signal_scores["attachment"] = attachment_norm[incident_id]
            signals.attachment = {"hit": True}
        if incident_id in relationship_norm:
            signal_scores["relationship"] = relationship_norm[incident_id]
            signals.relationship = {"via_expansion": True}

        results.append(
            SearchResult(
                incident_id=incident.id,
                title=incident.title,
                status=incident.status,
                fused_score=fuse(signal_scores, weights),
                signals=signals,
                snippet=snippets.get(incident_id),
            )
        )

    results.sort(key=lambda r: r.fused_score, reverse=True)
    return SearchResponse(
        query=query,
        results=results[:limit],
        degraded=SearchDegraded(vector_search=not vector_available),
        weights_profile=WEIGHTS_PROFILE,
    )
