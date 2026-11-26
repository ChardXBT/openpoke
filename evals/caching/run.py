"""Offline prompt-cache measurement harness.

The default fake model simulates a provider prompt cache: the first call with a stable
prefix is a miss and later calls with the same prefix are hits. It proves the measurement
path end-to-end without keys; pass a real transport/client around the same ``summarize``
function for live measurements.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from evals.harness.llm import Budget, LLMClient, LLMResult

STABLE_SYSTEM = "You are Poke. Use the stable working set below before the current turn."
STABLE_WORKING_SET = "#12 Tokyo Restaurant Search — done\n#13 Invoice follow-up — waiting"


class FakeCacheTransport:
    """Provider-shaped fake: first stable-prefix call misses, repeats hit."""

    def __init__(self, prompt_tokens: int = 1200, completion_tokens: int = 20) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.seen_prefixes: set[str] = set()
        self.calls = 0

    async def __call__(self, url: str, headers: dict, payload: dict) -> tuple[int, dict]:
        self.calls += 1
        messages = payload.get("messages") or []
        stable = messages[:-1] if messages and messages[-1].get("role") == "user" else messages
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        hit = digest in self.seen_prefixes
        self.seen_prefixes.add(digest)
        cached = self.prompt_tokens if hit else 0
        return 200, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached},
                "cache_creation_tokens": 0 if hit else self.prompt_tokens,
                "cost": 0,
            },
        }


def summarize(results: list[LLMResult]) -> dict:
    hits = sum(1 for result in results if result.cached_prompt_tokens > 0)
    cached_tokens = sum(result.cached_prompt_tokens for result in results)
    prompt_tokens = sum(result.prompt_tokens for result in results)
    return {
        "requests": len(results),
        "cache_hit_requests": hits,
        "cache_miss_requests": len(results) - hits,
        "cache_hit_rate": round(hits / len(results), 4) if results else 0.0,
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_tokens,
        "uncached_prompt_tokens": prompt_tokens - cached_tokens,
        "cache_creation_tokens": sum(result.cache_creation_tokens for result in results),
    }


async def measure(turns: int) -> tuple[dict, list[dict]]:
    if turns <= 0:
        raise ValueError("turns must be positive")
    transport = FakeCacheTransport()
    client = LLMClient("openai:fake-cache", Budget(1.0, Path(tempfile.gettempdir()) / "openpoke-fake-cache-ledger.json"), transport=transport)
    results: list[LLMResult] = []
    for turn in range(turns):
        messages = [
            {"role": "assistant", "content": STABLE_WORKING_SET},
            {"role": "user", "content": f"turn {turn}: what changed?"},
        ]
        results.append(await client.complete(STABLE_SYSTEM, messages, []))
    rows = [asdict(result) for result in results]
    for row in rows:
        row.pop("message", None)
    return summarize(results), rows


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary, rows = asyncio.run(measure(args.turns))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.out / "rows.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
