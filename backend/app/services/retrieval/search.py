"""Builds the API/MCP-facing SearchResponse from the retrieval signals available today
(lexical + trigram + attachment text). Vector/exact-match/relationship signals and
real score fusion are Phase 8/9 — see docs/ARCHITECTURE.md § 5. The response shape is
final; only `signals` and `degraded` will gain more populated fields as those land.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.schemas.search import SearchDegraded, SearchResponse, SearchResult, SearchSignals
from app.services.retrieval.lexical import search_attachment_text, search_lexical, search_trigram

_TRIGRAM_ONLY_SCORE = 0.05  # substring-only match, not lexically ranked
_ATTACHMENT_ONLY_SCORE = 0.03  # weaker: found only in an attachment, not the incident text


async def search_incidents(session: AsyncSession, query: str, limit: int = 20) -> SearchResponse:
    lexical_hits = await search_lexical(session, query, limit=limit)
    trigram_ids = set(await search_trigram(session, query, limit=limit))
    attachment_hits = await search_attachment_text(session, query, limit=limit)
    attachment_by_incident = {hit["incident_id"]: hit for hit in attachment_hits}

    lexical_ids = [hit["incident_id"] for hit in lexical_hits]
    seen_ids = set(lexical_ids)
    extra_trigram_ids = [i for i in trigram_ids if i not in seen_ids]
    seen_ids |= set(extra_trigram_ids)
    extra_attachment_ids = [i for i in attachment_by_incident if i not in seen_ids]

    all_ids = lexical_ids + extra_trigram_ids + extra_attachment_ids
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
        attachment_hit = attachment_by_incident.get(incident.id)
        results.append(
            SearchResult(
                incident_id=incident.id,
                title=incident.title,
                status=incident.status,
                fused_score=round(1.0 / (rank_position + 1), 4),
                signals=SearchSignals(
                    lexical={"bm25_raw": hit["bm25_rank"], "rank": rank_position},
                    trigram={"hit": True} if incident.id in trigram_ids else None,
                    attachment={"hit": True} if attachment_hit else None,
                ),
                snippet=hit["snippet"],
            )
        )

    for incident_id in extra_trigram_ids:
        incident = incidents.get(incident_id)
        if incident is None:
            continue
        attachment_hit = attachment_by_incident.get(incident_id)
        results.append(
            SearchResult(
                incident_id=incident.id,
                title=incident.title,
                status=incident.status,
                fused_score=_TRIGRAM_ONLY_SCORE,
                signals=SearchSignals(
                    trigram={"hit": True, "lexical_miss": True},
                    attachment={"hit": True} if attachment_hit else None,
                ),
                snippet=None,
            )
        )

    for incident_id in extra_attachment_ids:
        incident = incidents.get(incident_id)
        if incident is None:
            continue
        attachment_hit = attachment_by_incident[incident_id]
        results.append(
            SearchResult(
                incident_id=incident.id,
                title=incident.title,
                status=incident.status,
                fused_score=_ATTACHMENT_ONLY_SCORE,
                signals=SearchSignals(attachment={"hit": True}),
                snippet=attachment_hit["snippet"],
            )
        )

    return SearchResponse(query=query, results=results, degraded=SearchDegraded())
