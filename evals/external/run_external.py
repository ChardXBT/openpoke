"""Run setups on the external forks' held-out cases as one pooled, concurrent run.

    python -m evals.external.run_external --setups b0,b2,ours --model anthropic/claude-haiku-5.5 \
        --max-usd 3 --out evals/runs/external-haiku

Uses the same turn loop, grading, budget ledger and caps as `evals.harness.runner`; one JSONL
row per decision with `source` set to the fork's module name. Data is read from the pinned
local clones (see `evals/external/README.md`); nothing from the forks is executed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from evals.external import load_all
from evals.harness.grade import summarize
from evals.harness.llm import Budget, BudgetExceeded, LLMClient, budget_for
from evals.harness.runner import MultiBudget, make_setup, run_turn


async def run_external(setups, client, out: Path, concurrency: int = 10, limit: Optional[int] = None) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "rows.jsonl"
    sem, lock, stop = asyncio.Semaphore(concurrency), asyncio.Lock(), asyncio.Event()
    rows: list[dict] = []

    async def one(source: str, setup, world, scenario) -> None:
        async with sem:
            if stop.is_set():
                return
            try:
                row = await run_turn(setup, world, scenario, client, 0, out)
            except BudgetExceeded as exc:
                stop.set()
                print(f"STOP: {exc}")
                return
            except Exception as exc:  # one bad turn must not sink the run
                row = {"setup": setup.name, "model": client.model, "size": world.size, "scenario_id": scenario.id,
                       "family": scenario.family, "rep": 0, "gold_kind": scenario.gold.kind, "outcome": "error",
                       "error": str(exc)[:300], "prompt_tokens": 0, "completion_tokens": 0, "uncached_prompt_tokens": 0,
                       "cached_prompt_tokens": 0, "cache_creation_prompt_tokens": 0, "cache_cost_unknown": False,
                       "cost_usd": 0.0, "latency_s": 0.0}
            row["source"] = source
            async with lock:
                rows.append(row)
                with rows_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, default=str) + "\n")

    tasks = []
    for source, loaded in load_all().items():
        print(f"{source}: {loaded.loaded} cases loaded, skipped {dict(loaded.skipped)}")
        cases = [(w, s) for w, scenarios in loaded.worlds for s in scenarios]
        if limit:
            cases = cases[:limit]
        tasks += [one(source, setup, w, s) for setup in setups for w, s in cases]
    await asyncio.gather(*tasks)

    for source in sorted({r["source"] for r in rows}):
        subset = [dict(r, size=0) for r in rows if r["source"] == source]  # pool sizes per source
        for key, s in summarize(subset).items():
            print(f"{source:<18} {key.split('|')[0]:<10} n={s['n']:<4} acc={s['accuracy']:.3f} "
                  f"ci=({s['accuracy_ci'][0]:.2f},{s['accuracy_ci'][1]:.2f}) dup={s['duplicate_rate']:.2f} "
                  f"wrong={s['wrong_reuse_rate']:.2f} over_ask={s['over_ask_rate']:.2f} tok={s['prompt_tokens_mean']:.0f}")
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setups", default="b0,b2,ours")
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="cases per source (smoke runs)")
    parser.add_argument("--max-usd", type=float, default=3.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    budget = MultiBudget([budget_for(args.model), Budget(args.max_usd, out / "ledger.json")])
    client = LLMClient(args.model, budget)
    setups = [make_setup(s.strip()) for s in args.setups.split(",") if s.strip()]
    asyncio.run(run_external(setups, client, out, args.concurrency, args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
