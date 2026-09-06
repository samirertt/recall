"""Phase 15 retrieval benchmark (docs/RESEARCH.md § Retrieval Evaluation).

Loads the fixture corpus through the *real* ingestion path (create_incident — same
FTS5 triggers, same embedding pipeline a real capture would exercise), runs both a
`hybrid` and a `lexical_only` profile against the real search_incidents() function,
computes P@K/Recall@K/MRR/pairwise-environment-accuracy, and asserts against
thresholds.yaml. This is evaluation-as-regression-test, not an absolute-quality claim
— see the module docstring in fixtures/thresholds.yaml.

Deliberately a smaller corpus (~32 incidents, one confusable pair per required
category) than docs/RESEARCH.md's ~150-250 recommendation — see
IMPLEMENTATION_CHECKLIST.md for why, and what growing it would take (append YAML
entries; no harness changes needed).
"""

from pathlib import Path

import pytest
import yaml

from app.services.retrieval import search as search_module
from app.services.retrieval.search import search_incidents

FIXTURES_DIR = Path(__file__).parent / "retrieval" / "fixtures"


def _load_yaml(name: str):
    with open(FIXTURES_DIR / name) as f:
        return yaml.safe_load(f)


@pytest.fixture
def corpus():
    return _load_yaml("corpus.yaml")


@pytest.fixture
def queries():
    return _load_yaml("queries.yaml")


@pytest.fixture
def thresholds():
    return _load_yaml("thresholds.yaml")


async def _ingest_corpus(client, corpus: list[dict]) -> dict[str, int]:
    """Returns {fixture_id: real_incident_id}."""
    id_map = {}
    for entry in corpus:
        resp = await client.post(
            "/incidents",
            json={"raw_problem": entry["problem"], "raw_solution": entry.get("solution")},
        )
        id_map[entry["id"]] = resp.json()["incident"]["id"]
    return id_map


def _metrics_for_profile(results_by_query: list[tuple[dict, list[int]]]) -> dict:
    """`results_by_query` is [(query_spec, ranked_incident_ids), ...].

    Uses precision@1 rather than the more usual precision@5: this benchmark's
    queries mostly have exactly one "relevant" incident each (a realistic shape for
    "have I seen this exact problem before"), which caps precision@5 at 0.2
    regardless of ranking quality — a query with one relevant document ranked #1
    scores the same 0.2 as one ranked #5, making precision@5 structurally
    uninformative here. precision@1 ("is the top result actually right") plus
    recall@10 and MRR together give a meaningful picture for this corpus shape;
    a benchmark grown to have multiple relevant documents per query should
    reinstate precision@5.
    """
    precisions_at_1 = []
    recalls_at_10 = []
    reciprocal_ranks = []
    pairwise_correct = 0
    pairwise_total = 0

    for query, ranked_ids in results_by_query:
        relevant = set(query.get("relevant", []))
        if relevant:
            precisions_at_1.append(1.0 if ranked_ids[:1] and ranked_ids[0] in relevant else 0.0)
        top10 = ranked_ids[:10]
        if relevant:
            recalls_at_10.append(len([r for r in top10 if r in relevant]) / len(relevant))

        rank = next((i + 1 for i, r in enumerate(ranked_ids) if r in relevant), None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)

        for pair in query.get("must_outrank", []):
            pairwise_total += 1
            preferred, over = pair["preferred"], pair["over"]
            preferred_rank = next(
                (i for i, r in enumerate(ranked_ids) if r == preferred), len(ranked_ids)
            )
            over_rank = next((i for i, r in enumerate(ranked_ids) if r == over), len(ranked_ids))
            if preferred_rank < over_rank:
                pairwise_correct += 1

    def _avg(values):
        return sum(values) / len(values) if values else 0.0

    return {
        "precision_at_1": _avg(precisions_at_1),
        "recall_at_10": _avg(recalls_at_10),
        "mrr": _avg(reciprocal_ranks),
        "pairwise_env_accuracy": (pairwise_correct / pairwise_total) if pairwise_total else 1.0,
    }


async def _run_profile(session, id_map, queries):
    results_by_query = []
    for query in queries:
        translated = dict(query)
        translated["relevant"] = [id_map[fid] for fid in query.get("relevant", [])]
        translated["must_outrank"] = [
            {"preferred": id_map[p["preferred"]], "over": id_map[p["over"]]}
            for p in query.get("must_outrank", [])
        ]
        response = await search_incidents(session, query["query"], limit=10)
        ranked_ids = [r.incident_id for r in response.results]
        results_by_query.append((translated, ranked_ids))
    return _metrics_for_profile(results_by_query)


async def test_retrieval_benchmark_hybrid_and_lexical_only(
    client, db_session, corpus, queries, thresholds, monkeypatch
):
    id_map = await _ingest_corpus(client, corpus)

    hybrid_metrics = await _run_profile(db_session, id_map, queries)

    monkeypatch.setattr(search_module, "get_embedding_provider", lambda: None)
    lexical_metrics = await _run_profile(db_session, id_map, queries)

    report_lines = ["", "Retrieval benchmark results:"]
    for profile_name, metrics in (("hybrid", hybrid_metrics), ("lexical_only", lexical_metrics)):
        gates = thresholds[profile_name]
        report_lines.append(f"  {profile_name}: {metrics}")
        for metric_name, value in metrics.items():
            gate = gates[metric_name]
            status = "OK" if value >= gate else "FAIL"
            report_lines.append(f"    {metric_name}: {value:.3f} (gate {gate:.2f}) [{status}]")
    print("\n".join(report_lines))

    for profile_name, metrics in (("hybrid", hybrid_metrics), ("lexical_only", lexical_metrics)):
        gates = thresholds[profile_name]
        for metric_name, value in metrics.items():
            assert value >= gates[metric_name], (
                f"{profile_name}.{metric_name} = {value:.3f} fell below gate "
                f"{gates[metric_name]:.2f}\n" + "\n".join(report_lines)
            )
