"""One-off performance measurement against Section 58's targets: lexical <100ms,
hybrid <500ms. Seeds ~1000 synthetic incidents (with embeddings) directly via the
DB, then times repeated searches. Not part of the permanent test suite (too slow
to run on every CI invocation) -- results are recorded by hand in
IMPLEMENTATION_CHECKLIST.md / docs/SEARCH.md.

Run against a throwaway database, never the real one — it seeds 1,000 junk incidents:

    ENGMEM_DATABASE_PATH=/tmp/perf_bench.db uv run alembic upgrade head
    ENGMEM_DATABASE_PATH=/tmp/perf_bench.db uv run --directory backend python \
        ../scripts/perf_bench.py
    rm -f /tmp/perf_bench.db*
"""

import asyncio
import random
import statistics
import time

from app.db.session import get_session_factory
from app.models.incident import Incident
from app.services.embeddings.service import embed_incident
from app.services.retrieval.lexical import search_lexical
from app.services.retrieval.search import search_incidents

TECHS = ["CUDA", "Docker", "Kubernetes", "Postgres", "ROS2", "React", "Rust", "Bazel", "Terraform"]
VERBS = ["crashes", "hangs", "fails silently", "throws an error", "returns wrong data", "leaks memory"]
NOUNS = ["the build pipeline", "the training job", "the API server", "the deploy script", "the test suite"]


def _random_problem(i: int) -> str:
    tech = random.choice(TECHS)
    verb = random.choice(VERBS)
    noun = random.choice(NOUNS)
    return (
        f"Incident {i}: {noun} using {tech} {verb} under load. "
        f"Seen intermittently after upgrading {tech} on the CI runners, "
        f"error code E{i % 97}{i % 13} shows up in the logs."
    )


async def main():
    n = 1000
    print(f"Seeding {n} synthetic incidents (with embeddings)...")
    t0 = time.perf_counter()
    async with get_session_factory()() as session:
        for i in range(n):
            incident = Incident(raw_problem=_random_problem(i))
            session.add(incident)
            await session.commit()
            await embed_incident(session, incident)
            if (i + 1) % 200 == 0:
                print(f"  {i + 1}/{n} ({time.perf_counter() - t0:.1f}s elapsed)")
    print(f"Seeding done in {time.perf_counter() - t0:.1f}s")

    queries = [
        "CUDA build pipeline crashes under load",
        "Docker training job hangs after upgrade",
        "Postgres API server fails silently",
        "error code E42 in the logs",
        "Kubernetes deploy script returns wrong data",
        "Rust test suite leaks memory intermittently",
    ]

    async with get_session_factory()() as session:
        lexical_times = []
        for q in queries * 10:
            t0 = time.perf_counter()
            await search_lexical(session, q, limit=20)
            lexical_times.append((time.perf_counter() - t0) * 1000)

        hybrid_times = []
        for q in queries * 10:
            t0 = time.perf_counter()
            await search_incidents(session, q, limit=20)
            hybrid_times.append((time.perf_counter() - t0) * 1000)

    def report(name, times):
        times.sort()
        p50 = times[len(times) // 2]
        p95 = times[int(len(times) * 0.95)]
        print(f"{name}: n={len(times)} mean={statistics.mean(times):.1f}ms "
              f"p50={p50:.1f}ms p95={p95:.1f}ms max={max(times):.1f}ms")

    report("lexical", lexical_times)
    report("hybrid", hybrid_times)


asyncio.run(main())
