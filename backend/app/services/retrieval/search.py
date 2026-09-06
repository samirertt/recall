"""Builds the API/MCP-facing SearchResponse from the retrieval signals available today
(lexical + trigram). Vector/exact-match/relationship signals and real score fusion are
Phase 8/9 — see docs/ARCHITECTURE.md § 5. The response shape is final; only `signals`
and `degraded` will gain more populated fields as those phases land.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.schemas.search import SearchDegraded, SearchResponse, SearchResult, SearchSignals
from app.services.retrieval.lexical import search_lexical, search_trigram


async def search_incidents(session: AsyncSession, query: str, limit: int = 20) -> SearchResponse:
    lexical_hits = await search_lexical(session, query, limit=limit)
    trigram_ids = set(await search_trigram(session, query, limit=limit))

    lexical_ids = [hit["incident_id"] for hit in lexical_hits]
    extra_trigram_ids = [i for i in trigram_ids if i not in lexical_ids]
    all_ids = lexical_ids + extra_trigram_ids
    if not all_ids:
        return SearchResponse(query=query, results=[], degraded=SearchDegraded())

    incidents = {
        incident.id: incident
        for incident in (
            await session.execute(select(Incident).where(Incident.id.in_(all_ids)))
        )
        .scalars()
        .all()
    }

    results: list[SearchResult] = []
    for rank_position, hit in enumerate(lexical_hits):
        incident = incidents.get(hit["incident_id"])
        if incident is None:  # stale FTS row racing a delete — skip rather than 500
            continue
        results.append(
            SearchResult(
                incident_id=incident.id,
                title=incident.title,
                status=incident.status,
                fused_score=round(1.0 / (rank_position + 1), 4),
                signals=SearchSignals(
                    lexical={"bm25_raw": hit["bm25_rank"], "rank": rank_position},
                    trigram={"hit": True} if incident.id in trigram_ids else None,
                ),
                snippet=hit["snippet"],
            )
        )

    for incident_id in extra_trigram_ids:
        incident = incidents.get(incident_id)
        if incident is None:
            continue
        results.append(
            SearchResult(
                incident_id=incident.id,
                title=incident.title,
                status=incident.status,
                fused_score=0.05,  # low, fixed: substring-only match, not lexically ranked
                signals=SearchSignals(trigram={"hit": True, "lexical_miss": True}),
                snippet=None,
            )
        )

    return SearchResponse(query=query, results=results, degraded=SearchDegraded())
