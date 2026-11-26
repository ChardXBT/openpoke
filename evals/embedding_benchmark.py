"""Offline semantic-search storage benchmark for generated agent registries.

Uses a deterministic local embedder and temporary directories only. It never calls a
provider or needs keys. The default size is the 20,000-agent target from the scaling issue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from server.services.execution.registry import AgentRecord, AgentRegistry, Ref, normalize_name
from server.services.execution.search import search_agents

NOW = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)


def _iso(days: float) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def deterministic_embedder(dimensions: int, calls: list[int]):
    def embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([digest[index % len(digest)] / 255.0 for index in range(dimensions)])
        return vectors

    return embed


def build_registry(path: Path, agents: int) -> AgentRegistry:
    reg = AgentRegistry(path, clock=lambda: NOW)
    records = []
    refs: dict[int, list[Ref]] = {}
    for agent_id in range(1, agents + 1):
        topic = f"agent {agent_id} project research reminder"
        if agent_id == agents // 2:
            topic = "audit recurring subscriptions and duplicate payments"
        records.append(
            AgentRecord(
                agent_id,
                f"Generated agent {agent_id}",
                normalize_name(f"Generated agent {agent_id}"),
                _iso(2),
                _iso(1),
                "done",
                "",
                topic,
                1,
                False,
                False,
            )
        )
        if agent_id % 10 == 0:
            refs[agent_id] = [Ref("contact", f"person{agent_id}@example.test")]
    reg.bulk_insert(records, refs)
    return reg


def run_benchmark(agents: int = 20_000, dimensions: int = 64, keep: Optional[Path] = None) -> dict:
    if agents <= 0:
        raise ValueError("agents must be positive")
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    previous_cache_dir = os.environ.get("OPENPOKE_EMBEDDING_CACHE_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="openpoke-embedding-bench-") as temporary:
            root = Path(keep) if keep is not None else Path(temporary)
            root.mkdir(parents=True, exist_ok=True)
            cache = root / "vectors"
            os.environ["OPENPOKE_EMBEDDING_CACHE_DIR"] = str(cache)
            reg = build_registry(root / "agents.db", agents)
            calls: list[int] = []
            embedder = deterministic_embedder(dimensions, calls)
            query = "which subscriptions did you say i was twice paying for"

            started = time.perf_counter()
            cold = search_agents(reg, query, now=NOW, embedder=embedder)
            cold_seconds = time.perf_counter() - started
            cold_embedded_texts = sum(calls)
            cold_call_count = len(calls)

            tracemalloc.start()
            started = time.perf_counter()
            warm = search_agents(reg, query, now=NOW, embedder=embedder)
            warm_seconds = time.perf_counter() - started
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            warm_embedded_texts = sum(calls) - cold_embedded_texts

            cache_files = [path for path in cache.glob("**/*") if path.is_file()]
            return {
                "agents": agents,
                "dimensions": dimensions,
                "cold_seconds": round(cold_seconds, 4),
                "warm_seconds": round(warm_seconds, 4),
                "warm_tracemalloc_peak_mib": round(peak / 1024 / 1024, 3),
                "cold_embedder_calls": cold_call_count,
                "cold_embedded_texts": cold_embedded_texts,
                "warm_embedded_texts": warm_embedded_texts,
                "cache_files": len(cache_files),
                "cache_bytes": sum(path.stat().st_size for path in cache_files),
                "json_cache_files": len(list(cache.glob("*.json"))),
                "cold_first_hit": cold[0].agent.id if cold else None,
                "warm_first_hit": warm[0].agent.id if warm else None,
            }
    finally:
        if previous_cache_dir is None:
            os.environ.pop("OPENPOKE_EMBEDDING_CACHE_DIR", None)
        else:
            os.environ["OPENPOKE_EMBEDDING_CACHE_DIR"] = previous_cache_dir


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--agents", type=int, default=20_000)
    parser.add_argument("--dimensions", type=int, default=64)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    summary = run_benchmark(args.agents, args.dimensions)
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
