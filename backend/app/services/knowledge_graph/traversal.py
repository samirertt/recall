"""N-hop relationship traversal (Section 28/29; docs/RESEARCH.md § Knowledge Graph).

Plain `WITH RECURSIVE` over `incident_relations` — no graph database. Depth- and
cycle-bounded via an accumulated path string, exactly the pattern documented in
docs/RESEARCH.md, reused verbatim in shape for `technology_relations`.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TRAVERSAL_SQL = text(
    """
    WITH RECURSIVE frontier(incident_id, depth, path) AS (
      SELECT :start_id, 0, ',' || :start_id || ','
      UNION
      SELECT
        CASE WHEN r.source_incident_id = f.incident_id
             THEN r.target_incident_id ELSE r.source_incident_id END,
        f.depth + 1,
        f.path || (CASE WHEN r.source_incident_id = f.incident_id
                        THEN r.target_incident_id ELSE r.source_incident_id END) || ','
      FROM incident_relations r
      JOIN frontier f
        ON r.source_incident_id = f.incident_id OR r.target_incident_id = f.incident_id
      WHERE f.depth < :max_depth
        AND instr(f.path, ',' || (CASE WHEN r.source_incident_id = f.incident_id
                                       THEN r.target_incident_id ELSE r.source_incident_id END)
                          || ',') = 0
        AND (:relation_type IS NULL OR r.relation_type = :relation_type)
    )
    SELECT incident_id, MIN(depth) AS depth
    FROM frontier
    WHERE incident_id != :start_id
    GROUP BY incident_id
    ORDER BY depth
    """
)


async def related_incidents(
    session: AsyncSession,
    start_id: int,
    max_depth: int = 2,
    relation_type: str | None = None,
) -> list[tuple[int, int]]:
    """Returns [(incident_id, depth), ...] ordered by depth, excluding start_id."""
    result = await session.execute(
        _TRAVERSAL_SQL,
        {"start_id": start_id, "max_depth": max_depth, "relation_type": relation_type},
    )
    return [(row[0], row[1]) for row in result.fetchall()]


async def related_incident_ids(
    session: AsyncSession,
    start_id: int,
    max_depth: int = 2,
    relation_type: str | None = None,
) -> list[int]:
    return [
        incident_id
        for incident_id, _depth in await related_incidents(
            session, start_id, max_depth, relation_type
        )
    ]
