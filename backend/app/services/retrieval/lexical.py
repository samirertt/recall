"""Lexical search (Phase 7) — the retrieval floor that must always work with zero
external dependencies (docs/ARCHITECTURE.md § 5 step 2, § 10 degradation matrix).

Ranking weights below are an initial default, not yet the configurable
`ranking.weights.json` profile described in docs/RESEARCH.md § Hybrid Retrieval —
that formalization lands in Phase 9 alongside vector fusion.
"""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Column order in incidents_fts (see the initial Alembic migration):
# title, normalized_problem, symptoms, root_cause, solution, explanation,
# why_solution_worked, lesson_learned, raw_problem, raw_solution
_BM25_WEIGHTS = "10.0, 3.0, 5.0, 5.0, 3.0, 2.0, 2.0, 4.0, 6.0, 3.0"

_TOKEN_RE = re.compile(r"[\w#./:_-]+", re.UNICODE)


def _build_match_query(raw_query: str) -> str | None:
    """Quote each token individually so arbitrary user input can never be parsed as
    an FTS5 query operator (AND/OR/NOT, column filters, NEAR, parentheses, `*`) —
    only as literal text to search for. Returns None if there is nothing searchable."""
    tokens = _TOKEN_RE.findall(raw_query)
    if not tokens:
        return None
    return " ".join('"' + token.replace('"', '""') + '"' for token in tokens)


_LEXICAL_SQL = text(
    f"""
    SELECT incidents_fts.rowid AS incident_id,
           bm25(incidents_fts, {_BM25_WEIGHTS}) AS rank,
           snippet(incidents_fts, -1, '**', '**', '...', 10) AS snippet
    FROM incidents_fts
    WHERE incidents_fts MATCH :match_query
    ORDER BY rank
    LIMIT :limit
    """
)

_TRIGRAM_SQL = text(
    """
    SELECT DISTINCT rowid AS incident_id
    FROM incidents_fts_trigram
    WHERE incidents_fts_trigram MATCH :match_query
    LIMIT :limit
    """
)


async def search_lexical(
    session: AsyncSession, query: str, limit: int = 20
) -> list[dict]:
    """Returns [{incident_id, bm25_rank, snippet}], best match first (bm25 ascending)."""
    match_query = _build_match_query(query)
    if match_query is None:
        return []
    result = await session.execute(_LEXICAL_SQL, {"match_query": match_query, "limit": limit})
    return [
        {"incident_id": row.incident_id, "bm25_rank": row.rank, "snippet": row.snippet}
        for row in result.fetchall()
    ]


async def search_trigram(session: AsyncSession, query: str, limit: int = 20) -> list[int]:
    """Substring/identifier search over raw problem+solution text — catches hex
    addresses, partial UUIDs, mangled log lines that word-tokenized FTS misses."""
    match_query = _build_match_query(query)
    if match_query is None:
        return []
    result = await session.execute(_TRIGRAM_SQL, {"match_query": match_query, "limit": limit})
    return [row.incident_id for row in result.fetchall()]
