"""Decision extraction, grading, and summary statistics with bootstrap intervals."""

from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Optional

from evals.schema import Scenario
from server.services.execution.resolver import is_irreversible

OUTCOMES = ("correct", "duplicate", "wrong_reuse", "missed_ask", "over_ask", "other")
INBOUND_FAMILY = 8  # email-event scenarios: informing the user without dispatch is acceptable


@dataclass
class Decision:
    kind: str  # "agent" | "NEW" | "ASK" | "OTHER"
    agent_id: Optional[int] = None
    dispatches: int = 0
    asked: bool = False
    irreversible_dispatch: bool = False
    targets: list = field(default_factory=list)  # every submitted dispatch: agent id or "NEW"


def extract_decision(
    calls: list[dict], user_msgs: list[str], resolve_target: Callable[[str], Optional[int]]
) -> Decision:
    """``calls``: send_message_to_agent calls as {"args": {...}, "status": "..."}."""
    submitted = [c for c in calls if c.get("status") == "submitted"]
    asked = any("?" in (m or "") for m in user_msgs)
    targets: list = []
    irreversible = False
    for call in submitted:
        args = call.get("args") or {}
        agent_id = call.get("resolved_id")
        if agent_id is None and not call.get("new_agent_created"):
            agent_id = resolve_target(str(args.get("agent_name", "")))
        targets.append(agent_id if agent_id is not None else "NEW")
        irreversible = irreversible or is_irreversible(str(args.get("instructions", "")))
    if submitted:
        last = targets[-1]
        if last == "NEW":
            return Decision("NEW", None, len(submitted), asked, irreversible, targets)
        return Decision("agent", int(last), len(submitted), asked, irreversible, targets)
    if asked:
        return Decision("ASK", None, 0, True, False, targets)
    return Decision("OTHER", None, 0, False, False, targets)


def grade(decision: Decision, scenario: Scenario) -> str:
    gold = scenario.gold
    ok_agents = {gold.agent_id, *gold.acceptable} - {None}
    if gold.kind == "ASK":
        if decision.kind == "ASK" or (decision.asked and not decision.irreversible_dispatch):
            return "correct"
        if decision.kind in ("agent", "NEW"):
            return "missed_ask"
        return "other"
    if scenario.family == INBOUND_FAMILY and decision.kind in ("OTHER", "ASK"):
        # Told the user (often offering a next step as a question), created nothing,
        # touched nothing wrong: the right behaviour for a watcher notification.
        return "correct"
    if decision.kind == "ASK":
        return "over_ask"
    if gold.kind == "NEW":
        if decision.kind == "NEW":
            return "correct"
        return "wrong_reuse" if decision.kind == "agent" else "other"
    # gold is an existing agent
    if decision.kind == "agent":
        return "correct" if decision.agent_id in ok_agents else "wrong_reuse"
    if decision.kind == "NEW":
        return "duplicate"
    return "other"


def _bootstrap(values: list[float], n: int = 1000, seed: int = 7) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return (means[int(0.025 * n)], means[int(0.975 * n) - 1])


def summarize(rows: Iterable[dict]) -> dict:
    """Group by (setup, model, size) → rates with 95% bootstrap intervals."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["setup"], row["model"], row["size"]), []).append(row)
    out = {}
    for key, items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        scored = [r for r in items if r["outcome"] in OUTCOMES]
        correct = [1.0 if r["outcome"] == "correct" else 0.0 for r in scored]
        ask_rows = [r for r in scored if r["gold_kind"] == "ASK"]
        non_ask = [r for r in scored if r["gold_kind"] != "ASK"]
        reuse_rows = [r for r in scored if r["gold_kind"] == "agent"]
        new_rows = [r for r in scored if r["gold_kind"] == "NEW"]
        lat = sorted(r["latency_s"] for r in scored if r.get("latency_s"))
        summary = {
            "n": len(scored),
            "does_not_fit": sum(1 for r in items if r["outcome"] == "does_not_fit"),
            "errors": sum(1 for r in items if r["outcome"] == "error"),
            "accuracy": statistics.fmean(correct) if correct else float("nan"),
            "accuracy_ci": _bootstrap(correct),
            "duplicate_rate": _rate(reuse_rows, "duplicate"),
            "wrong_reuse_rate": _rate(reuse_rows + new_rows, "wrong_reuse"),
            "false_reuse_rate": _rate(new_rows, "wrong_reuse"),
            "clarify_recall": _rate(ask_rows, "correct"),
            "over_ask_rate": _rate(non_ask, "over_ask"),
            "missed_ask_rate": _rate(ask_rows, "missed_ask"),
            "prompt_tokens_mean": statistics.fmean([r["prompt_tokens"] for r in scored]) if scored else 0,
            "latency_p50": lat[len(lat) // 2] if lat else 0,
            "latency_p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0,
            "cost_per_turn": statistics.fmean([r["cost_usd"] for r in scored]) if scored else 0,
            "by_family": _by_family(scored),
        }
        out["|".join(str(k) for k in key)] = summary
    return out


def _rate(rows: list[dict], outcome: str) -> float:
    return (sum(1 for r in rows if r["outcome"] == outcome) / len(rows)) if rows else float("nan")


def _by_family(rows: list[dict]) -> dict:
    fam: dict[int, list[float]] = {}
    for r in rows:
        fam.setdefault(r["family"], []).append(1.0 if r["outcome"] == "correct" else 0.0)
    return {k: round(statistics.fmean(v), 3) for k, v in sorted(fam.items())}


def paired_delta(rows_a: list[dict], rows_b: list[dict], n: int = 1000, seed: int = 11) -> tuple[float, float, float, int]:
    """Mean(correct_a − correct_b) over scenarios both setups ran, with a 95% bootstrap interval."""
    def key(r):
        return (r["scenario_id"], r["size"], r.get("rep", 0), r["model"])

    b_by_key = {key(r): r for r in rows_b if r["outcome"] in OUTCOMES}
    diffs = [
        (1.0 if a["outcome"] == "correct" else 0.0) - (1.0 if b_by_key[key(a)]["outcome"] == "correct" else 0.0)
        for a in rows_a if a["outcome"] in OUTCOMES and key(a) in b_by_key
    ]
    if not diffs:
        return (float("nan"), float("nan"), float("nan"), 0)
    lo, hi = _bootstrap(diffs, n, seed)
    return (statistics.fmean(diffs), lo, hi, len(diffs))


def decision_dict(decision: Decision) -> dict:
    return asdict(decision)
