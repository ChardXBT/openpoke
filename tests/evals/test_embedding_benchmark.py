from evals.embedding_benchmark import run_benchmark


def test_embedding_benchmark_runs_offline_on_generated_agents():
    summary = run_benchmark(agents=200, dimensions=4)
    assert summary["agents"] == 200
    assert summary["cold_embedded_texts"] > 0
    assert summary["warm_embedded_texts"] == 0
    assert summary["json_cache_files"] == 0
    assert summary["cache_files"] <= 3
    assert summary["cold_seconds"] >= 0 and summary["warm_seconds"] >= 0
