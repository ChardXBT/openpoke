"""Run setups × roster sizes × scenarios through a live model and grade each decision.

Example:
    python -m evals.harness.runner --setups b0,b2,ours --split dev --sizes 30,200,1000 \
        --model anthropic/claude-haiku-5.5 --max-usd 3 --out evals/runs/dev-haiku-1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from evals.harness.grade import answer_matches, extract_decision, grade, summarize
from evals.harness.llm import Budget, BudgetExceeded, LLMClient, budget_for
from evals.schema import Scenario, Setup, World
from evals.setups.common import parse_agent_id

SPLIT_SEEDS = {"dev": 1, "heldout": 2, "heldout2": 3}
GUARD_STATUSES = {"needs_user_choice", "confirm_target", "owned_by", "possible_duplicate", "error"}
MAX_ITERATIONS = 4


class MultiBudget(Budget):
    """Check and charge several budgets at once (the provider ledger and this run's cap)."""

    def __init__(self, budgets: list[Budget]) -> None:  # noqa: D107 - no super().__init__
        self.budgets = budgets

    def spent(self) -> float:
        return self.budgets[-1].spent()

    def check(self, est_usd: float) -> None:
        for b in self.budgets:
            b.check(est_usd)

    def charge(self, usd: float) -> None:
        for b in self.budgets:
            b.charge(usd)


HELDOUT_HASH_FILE = Path(__file__).resolve().parents[1] / "results" / "heldout.sha256"


def heldout_hash_ok(scenarios: list[Scenario]) -> bool:
    """The held-out set was frozen by hash before any held-out run; check it is unchanged."""
    import hashlib

    if not HELDOUT_HASH_FILE.exists():
        return False
    blob = json.dumps([asdict(s) for s in scenarios], sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest() == HELDOUT_HASH_FILE.read_text(encoding="utf-8").split()[0]


ROOT = Path(__file__).resolve().parents[2]
HELDOUT2_FREEZE = ROOT / "evals" / "results" / "heldout2.sha256"
HELDOUT2_RUNS = ROOT / "evals" / "results" / "heldout2.runs.jsonl"
# Everything that decides a routing outcome: product routing code, the setups, the harness and
# the world. A held-out split frozen with this digest can't be run on code changed after it.
FROZEN_CODE = ("server/services/execution", "server/agents/interaction_agent", "server/services/profile",
               "evals/setups", "evals/harness", "evals/world", "evals/schema.py")
NOT_FROZEN = {"evals/harness/report.py", "evals/harness/prompt_sizes.py"}  # reporting only


def code_digest(root: Path = ROOT) -> str:
    import hashlib

    files: list[Path] = []
    for rel in FROZEN_CODE:
        path = root / rel
        files += [path] if path.is_file() else sorted(path.rglob("*"))
    digest = hashlib.sha256()
    for path in sorted(f for f in files if f.is_file() and f.suffix in {".py", ".md"}):
        rel = path.relative_to(root).as_posix()
        if rel in NOT_FROZEN or "__pycache__" in rel:
            continue
        digest.update(rel.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def scenario_digest(scenarios: list[Scenario]) -> str:
    import hashlib

    blob = json.dumps([asdict(s) for s in scenarios], sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def heldout2_check(scenarios: list[Scenario], model: str, setups: list[str], sizes: list[int]) -> Optional[str]:
    """Why heldout2 must not run now (None: it may). Frozen scenarios + code, and each
    (model, setup, size) cell runs once."""
    if not HELDOUT2_FREEZE.exists():
        return "heldout2 is not frozen yet (evals/results/heldout2.sha256)"
    frozen = [line.split()[0] for line in HELDOUT2_FREEZE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if scenario_digest(scenarios) != frozen[0]:
        return "heldout2 scenarios changed since the freeze"
    if code_digest() != frozen[1]:
        return "routing code changed since heldout2 was frozen; it can't be run on tuned code"
    done = set()
    if HELDOUT2_RUNS.exists():
        for line in HELDOUT2_RUNS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done |= {(r["model"], st, sz) for st in r["setups"] for sz in r["sizes"]}
    repeats = sorted(c for c in done if c[0] == model and c[1] in setups and c[2] in sizes)
    if repeats:
        return f"heldout2 already ran {repeats}; it runs once"
    return None


def make_setup(name: str) -> Setup:
    from evals.setups.b0_names import B0Names
    from evals.setups.b1_cards import B1Cards
    from evals.setups.ours import Ours

    if name == "b0":
        return B0Names()
    if name == "b1":
        return B1Cards()
    if name == "b2":
        from evals.setups.b2_common import B2Common

        return B2Common(embeddings=bool(os.getenv("OPENAI_API_KEY", "").strip()))
    if name == "b2-lexical":
        from evals.setups.b2_common import B2Common

        return B2Common(embeddings=False)
    if name == "ours":
        return Ours()
    if name.startswith("ours-no_"):
        return Ours(set(name[len("ours-no_"):].split("+")))
    raise ValueError(f"unknown setup {name}")


def _tool_message(call: dict, name: str, args: dict, result: dict) -> dict:
    """Mirror InteractionAgentRuntime._format_tool_result so models see the real format."""
    failed = result.get("status") in GUARD_STATUSES
    payload = {"tool": name, "status": "error" if failed else "success", "arguments": args,
               ("error" if failed else "result"): result}
    return {"role": "tool", "tool_call_id": call.get("id") or name, "content": json.dumps(payload, default=str)}


async def run_turn(setup: Setup, world: World, scenario: Scenario, client, rep: int, workdir: Path) -> dict:
    base = {
        "setup": setup.name, "model": client.model, "split": scenario.split, "size": world.size,
        "scenario_id": scenario.id, "family": scenario.family, "rep": rep,
        "gold_kind": scenario.gold.kind, "gold_agent": scenario.gold.agent_id,
        "irreversible": scenario.irreversible,
        "prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0,
        "cache_creation_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0,
    }
    prepared = setup.prepare(world, scenario, workdir)
    base["prompt_chars"] = prepared.prompt_chars
    if prepared.prompt_chars / 3.5 > client.context_window() * 0.9:
        return {**base, "outcome": "does_not_fit"}

    messages = list(prepared.messages)
    calls: list[dict] = []
    user_msgs: list[str] = []
    statuses: list[str] = []
    searches = 0
    iterations = 0
    for iterations in range(1, MAX_ITERATIONS + 1):
        res = await client.complete(prepared.system_prompt, messages, prepared.tools)
        base["prompt_tokens"] += res.prompt_tokens
        base["cached_prompt_tokens"] += res.cached_prompt_tokens
        base["cache_creation_tokens"] += res.cache_creation_tokens
        base["completion_tokens"] += res.completion_tokens
        base["cost_usd"] += res.cost_usd
        base["latency_s"] += res.latency_s
        msg = res.message or {}
        tool_calls = msg.get("tool_calls") or []
        entry = {"role": "assistant", "content": msg.get("content") or ""}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        messages.append(entry)
        if not tool_calls:
            if (msg.get("content") or "").strip():
                user_msgs.append(msg["content"])
            break
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            result = prepared.handle_tool(name, args)
            if name == "send_message_to_agent":
                status = result.get("status")
                statuses.append(status)
                resolved = result.get("resolved_id")
                # A setup that sets resolved_id (even to None: created this turn) has decided.
                if "resolved_id" not in result and status == "submitted" and not result.get("new_agent_created"):
                    resolved = parse_agent_id(str(result.get("agent", "")))
                calls.append({"args": args, "status": status, "resolved_id": resolved,
                              "new_agent_created": result.get("new_agent_created")})
            elif name == "send_message_to_user":
                user_msgs.append(str(args.get("message", "")))
            elif name == "search_agents":
                searches += 1
            messages.append(_tool_message(call, name, args, result))

    decision = extract_decision(calls, user_msgs, prepared.resolve_target)
    if decision.dispatches == 0 and scenario.gold.agent_id is not None and user_msgs:
        gold = next((a for a in world.agents if a.id == scenario.gold.agent_id), None)
        if gold is not None:
            decision.answered = answer_matches(f"{gold.state_note} {gold.topic}", user_msgs[-1],
                                               said=scenario.message or "")
    outcome = grade(decision, scenario)
    return {
        **base, "outcome": outcome, "decision": asdict(decision), "guard_statuses": statuses,
        "searches": searches, "iterations": iterations,
        "dispatch_names": [c["args"].get("agent_name") for c in calls],
        "last_user_message": (user_msgs[-1][:300] if user_msgs else ""),
        "cost_usd": round(base["cost_usd"], 6), "latency_s": round(base["latency_s"], 3),
    }


async def run(
    setups: list[Setup],
    split: str,
    sizes: list[int],
    client,
    out: Path,
    reps: int = 1,
    concurrency: int = 8,
    limit: Optional[int] = None,
    families: Optional[set[int]] = None,
    sample: Optional[int] = None,
    world_factory: Optional[Callable[[int, int], World]] = None,
    scenario_factory: Optional[Callable[[World], list[Scenario]]] = None,
    log: Callable[[str], None] = print,
) -> Path:
    if world_factory is None or scenario_factory is None:
        from evals.world.generate import build_world
        from evals.world.scenarios import build_scenarios

        world_factory = world_factory or build_world
        scenario_factory = scenario_factory or build_scenarios
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "rows.jsonl"
    seed = SPLIT_SEEDS[split]
    sem = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()
    lock = asyncio.Lock()
    rows: list[dict] = []

    async def one(setup: Setup, world: World, scenario: Scenario, rep: int) -> None:
        async with sem:
            if stop.is_set():
                return
            try:
                row = await run_turn(setup, world, scenario, client, rep, out)
            except BudgetExceeded as exc:
                stop.set()
                log(f"STOP: {exc}")
                return
            except Exception as exc:  # one bad turn must not sink the run
                row = {"setup": setup.name, "model": client.model, "split": split, "size": world.size,
                       "scenario_id": scenario.id, "family": scenario.family, "rep": rep,
                       "gold_kind": scenario.gold.kind, "outcome": "error", "error": str(exc)[:500],
                       "prompt_tokens": 0, "cached_prompt_tokens": 0, "cache_creation_tokens": 0,
                       "cost_usd": 0.0, "latency_s": 0.0}
            async with lock:
                rows.append(row)
                with rows_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, default=str) + "\n")

    started = time.time()
    if split == "heldout2":
        first = scenario_factory(world_factory(sizes[0], seed))
        reason = heldout2_check(first, client.model, [s.name for s in setups], sizes)
        if reason:
            raise SystemExit(f"refusing to run: {reason}")
        HELDOUT2_RUNS.parent.mkdir(parents=True, exist_ok=True)
        with HELDOUT2_RUNS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"model": client.model, "setups": [s.name for s in setups], "sizes": sizes,
                                 "out": out.as_posix(), "code": code_digest()[:12],
                                 "started": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
    for size in sizes:
        world = world_factory(size, seed)
        all_scenarios = scenario_factory(world)
        if split == "heldout" and not heldout_hash_ok(all_scenarios):
            raise SystemExit("held-out scenarios do not match evals/results/heldout.sha256; refusing to run")
        scenarios = [s for s in all_scenarios if not families or s.family in families]
        if limit:
            scenarios = scenarios[:limit]
        if sample and sample < len(scenarios):
            # Evenly spaced across the list (which is ordered by family), so every family is
            # represented; --limit takes the first N and covers only the first families.
            step = len(scenarios) / sample
            scenarios = [scenarios[int(i * step)] for i in range(sample)]
        log(f"size {size}: {len(scenarios)} scenarios x {len(setups)} setups x {reps} reps")
        tasks = [one(setup, world, s, rep) for setup in setups for s in scenarios for rep in range(reps)]
        await asyncio.gather(*tasks)
        if stop.is_set():
            break
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log(f"done in {time.time() - started:.0f}s, {len(rows)} rows, spent ${client.budget.spent():.4f} this run")
    for key, s in summary.items():
        log(f"{key:<60} n={s['n']:<4} acc={s['accuracy']:.3f} ci=({s['accuracy_ci'][0]:.2f},{s['accuracy_ci'][1]:.2f}) "
            f"dup={s['duplicate_rate']:.2f} wrong={s['wrong_reuse_rate']:.2f} ask_recall={s['clarify_recall']:.2f} "
            f"over_ask={s['over_ask_rate']:.2f} tok={s['prompt_tokens_mean']:.0f} $={s['cost_per_turn']:.4f}")
    return rows_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--setups", default="b0,b2,ours")
    parser.add_argument("--split", choices=sorted(SPLIT_SEEDS), default="dev")
    parser.add_argument("--sizes", default="30,200,1000")
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sample", type=int, default=None, help="evenly spaced subset covering every family")
    parser.add_argument("--families", default="")
    parser.add_argument("--max-usd", type=float, default=5.0, help="cap for this run (the provider ledger cap still applies)")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.out)
    budget = MultiBudget([budget_for(args.model), Budget(args.max_usd, run_dir / "ledger.json")])
    client = LLMClient(args.model, budget, max_tokens=args.max_tokens)
    setups = [make_setup(s.strip()) for s in args.setups.split(",") if s.strip()]
    families = {int(f) for f in args.families.split(",") if f.strip()} or None
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    (run_dir).mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(vars(args), indent=2), encoding="utf-8")
    asyncio.run(run(setups, args.split, sizes, client, run_dir, reps=args.reps, concurrency=args.concurrency,
                    limit=args.limit, families=families, sample=args.sample))
    return 0


if __name__ == "__main__":
    sys.exit(main())
