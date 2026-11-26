"""Frozen offline quality eval for paraphrase search and multi-turn memory plumbing.

``cases.json`` is hash-frozen; the script refuses to run if it changes. The memory model is
scripted by the case itself, so the baseline is deterministic and needs no keys. It measures
whether exact matches stay first, paraphrase recall works, prior profile state is carried into
later extraction prompts, and multi-turn updates/retirements land in the store.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from server.config import Settings
from server.services.execution.registry import AgentRecord, AgentRegistry, Ref, normalize_name
from server.services.execution.search import search_agents
from server.services.profile import learn as learn_mod
from server.services.profile.store import ProfileStore, _override as profile_override

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "cases.json"
HASH_PATH = ROOT / "cases.sha256"
NOW = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)


def cases_hash() -> str:
    return hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()


def verify_frozen() -> None:
    expected = HASH_PATH.read_text(encoding="utf-8").split()[0] if HASH_PATH.exists() else ""
    if not expected or cases_hash() != expected:
        raise SystemExit("frozen eval cases do not match evals/frozen/cases.sha256; refusing to run")


def _iso(days: float) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(i, name, topic="", note="", days=1.0):
    return AgentRecord(i, name, normalize_name(name), _iso(days + 1), _iso(days), "done", note, topic, 1, False, False)


def search_registry() -> AgentRegistry:
    reg = AgentRegistry(Path(":memory:"), clock=lambda: NOW)
    reg.bulk_insert([
        _rec(1, "Email to Alice", "Draft the Q3 deck email to Alice", "draft to alice.chen@acme.com", 0.1),
        _rec(2, "Tokyo Restaurant Search", "Find good ramen and sushi spots in Shinjuku", days=20),
        _rec(3, "Invoice follow-up", "Chase Bob about the unpaid invoice", days=2),
        _rec(20, "Food pantry donation", "Organize the neighborhood food donation list", days=0.2),
        _rec(30, "Zoë Öberg introduction", "Introduce Zoë Öberg to the design team", days=1),
    ], {
        1: [Ref("contact", "alice.chen@acme.com")],
        3: [Ref("contact", "bob@northwind.com"), Ref("subject", "Invoice #402")],
        30: [Ref("contact_name", "Zoë Öberg")],
    })
    return reg


def run_search_cases(cases: list[dict]) -> dict:
    reg = search_registry()
    rows = []
    for case in cases:
        hits = search_agents(reg, case["query"], now=NOW, limit=8)
        ids = [hit.agent.id for hit in hits]
        ok = True
        if "expect_count" in case:
            ok = len(hits) == case["expect_count"]
        if "expect_first" in case:
            ok = bool(ids) and ids[0] == case["expect_first"]
        if "expect_contains" in case:
            ok = ok and all(item in ids for item in case["expect_contains"])
        rows.append({"id": case["id"], "passed": ok, "ids": ids})
    return {"passed": sum(row["passed"] for row in rows), "total": len(rows), "rows": rows}


async def _learn_with_script(store: ProfileStore, message: str, context: str, reply: dict, prompts: list[str]) -> int:
    async def fake_complete(model, messages, system, max_tokens):
        prompts.append(messages[-1]["content"])
        return {"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}

    return await learn_mod.learn_from_message(message, "user", context=context, complete=fake_complete)


async def run_memory_cases_async(cases: list[dict], tmp: Path) -> dict:
    rows = []
    original_settings = learn_mod.get_settings
    learn_mod.get_settings = lambda: Settings(learn_enabled=True, memory_model="scripted-fake")
    try:
        for case in cases:
            store = ProfileStore(tmp / f"{case['id']}.db")
            token = profile_override.set(store)
            prompts: list[str] = []
            try:
                case_ok = True
                turn_rows = []
                for index, turn in enumerate(case["turns"]):
                    await _learn_with_script(store, turn["message"], turn.get("context", ""), turn["reply"], prompts)
                    current = store.current()
                    text = "\n".join(f"{fact.subject} {fact.value} {fact.email or ''}".casefold() for fact in current)
                    ok = all(needle.casefold() in text for needle in turn.get("expect_text", []))
                    if "expect_count" in turn:
                        ok = len(current) == turn["expect_count"]
                    for needle in turn.get("expect_prompt_contains", []):
                        ok = ok and needle.casefold() in prompts[index].casefold()
                    turn_rows.append({"passed": ok, "facts": [fact.line() for fact in current]})
                    case_ok = case_ok and ok
                rows.append({"id": case["id"], "passed": case_ok, "turns": turn_rows})
            finally:
                profile_override.reset(token)
    finally:
        learn_mod.get_settings = original_settings
    return {"passed": sum(row["passed"] for row in rows), "total": len(rows), "rows": rows}


def summarize(cases: dict, tmp: Path) -> dict:
    search = run_search_cases(cases["search_cases"])
    memory = asyncio.run(run_memory_cases_async(cases["memory_cases"], tmp))
    total_passed = search["passed"] + memory["passed"]
    total = search["total"] + memory["total"]
    return {
        "frozen_hash": cases_hash(),
        "passed": total_passed,
        "total": total,
        "pass_rate": round(total_passed / total, 4) if total else 0.0,
        "search": search,
        "memory": memory,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    verify_frozen()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    summary = summarize(cases, args.out / "work")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("passed", "total", "pass_rate")}, sort_keys=True))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
