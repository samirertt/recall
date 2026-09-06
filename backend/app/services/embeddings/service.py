"""Embedding generation + NumPy brute-force vector search (Phase 8).

docs/RESEARCH.md § Vector Index: a plain NumPy brute-force cosine table is the
correct v1 at this project's scale (comfortably under 100k rows) — exact, zero new
dependencies beyond numpy (already required for embeddings), and trivially
rebuildable. `sqlite-vec` is the documented upgrade path if this ever stops being
"fast enough," which is not expected at a personal-incident-history scale.
"""

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timing import log_duration
from app.models.embedding import ChunkEmbedding
from app.models.incident import Attempt, Incident
from app.services.embeddings.provider import EmbeddingProvider, get_embedding_provider

CHUNK_KEY = "full"


def compose_incident_text(incident: Incident, attempts: list[Attempt] = ()) -> str:
    """Section 23: incorporate problem/symptoms/root-cause/solution/failed-attempts/
    lessons into the embedding context — not just the raw problem description.

    Takes `attempts` explicitly rather than reading `incident.attempts`: the caller
    may pass an `Incident` whose `attempts` relationship was never eagerly loaded (a
    freshly-constructed or bulk-queried instance), and touching that lazy-loaded
    collection here would trigger an implicit sync DB call with no greenlet context
    (`sqlalchemy.exc.MissingGreenlet`) since this is plain attribute access, not an
    awaited session method.
    """
    parts: list[str] = [f"Problem: {incident.raw_problem}"]
    if incident.symptoms:
        parts.append(f"Symptoms: {incident.symptoms}")
    if incident.root_cause:
        parts.append(f"Root cause: {incident.root_cause}")
    solution = incident.solution or incident.raw_solution
    if solution:
        parts.append(f"Solution: {solution}")
    if incident.why_solution_worked:
        parts.append(f"Why it worked: {incident.why_solution_worked}")
    if incident.lesson_learned:
        parts.append(f"Lesson learned: {incident.lesson_learned}")
    for attempt in attempts:
        line = f"Failed attempt: {attempt.action}"
        if attempt.result:
            line += f" -> {attempt.result}"
        parts.append(line)
    return "\n".join(parts)


def _vector_to_bytes(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _bytes_to_vector(data: bytes, dims: int) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32, count=dims)


async def embed_incident(
    session: AsyncSession, incident: Incident, provider: EmbeddingProvider | None = None
) -> bool:
    """Embeds one incident, replacing any existing embedding for the current model.
    Returns False (no-op) if no embedding provider is available — never raises."""
    provider = provider or get_embedding_provider()
    if provider is None:
        return False

    with log_duration("embed_incident", incident_id=incident.id):
        attempts_result = await session.execute(
            select(Attempt).where(Attempt.incident_id == incident.id).order_by(Attempt.order)
        )
        text = compose_incident_text(incident, attempts_result.scalars().all())
        vector = provider.embed_document(text)

    await session.execute(
        delete(ChunkEmbedding).where(
            ChunkEmbedding.incident_id == incident.id,
            ChunkEmbedding.chunk_key == CHUNK_KEY,
            ChunkEmbedding.model_name == provider.model_name,
        )
    )
    session.add(
        ChunkEmbedding(
            incident_id=incident.id,
            chunk_key=CHUNK_KEY,
            embedding=_vector_to_bytes(vector),
            dims=provider.dims,
            model_name=provider.model_name,
            model_revision=provider.model_revision,
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return True


async def rebuild_embeddings(session: AsyncSession) -> dict:
    """The `rebuild-embeddings` maintenance command (Section 20/68/Phase 8) — always
    safe to run: embeddings are 100% derived from canonical incident text."""
    provider = get_embedding_provider()
    if provider is None:
        return {"embedded": 0, "skipped": 0, "reason": "no embedding provider available"}

    result = await session.execute(select(Incident))
    incidents = result.scalars().all()
    embedded = 0
    for incident in incidents:
        if await embed_incident(session, incident, provider=provider):
            embedded += 1
    return {
        "embedded": embedded,
        "skipped": len(incidents) - embedded,
        "model": provider.model_name,
    }


async def search_vector(
    session: AsyncSession, query: str, limit: int = 20
) -> list[dict]:
    """Brute-force cosine search over all current-model embeddings. Returns
    [{incident_id, cosine_score}] sorted best-first. Empty (not an error) if no
    provider is available or no incidents have been embedded yet."""
    provider = get_embedding_provider()
    if provider is None:
        return []

    result = await session.execute(
        select(ChunkEmbedding).where(
            ChunkEmbedding.chunk_key == CHUNK_KEY,
            ChunkEmbedding.model_name == provider.model_name,
        )
    )
    rows = result.scalars().all()
    if not rows:
        return []

    matrix = np.stack([_bytes_to_vector(row.embedding, row.dims) for row in rows])
    query_vector = provider.embed_query(query)
    scores = matrix @ query_vector  # both L2-normalized -> dot product == cosine similarity

    top_indices = np.argsort(-scores)[:limit]
    return [
        {"incident_id": rows[i].incident_id, "cosine_score": float(scores[i])}
        for i in top_indices
    ]
