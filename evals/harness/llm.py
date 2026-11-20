"""Model client for evals: OpenRouter or OpenAI direct, with a persistent spend ledger.

Model ids: ``anthropic/claude-haiku-5.5`` (OpenRouter) or ``openai:gpt-6-luna``
(OpenAI API directly, using OPENAI_API_KEY). Every call sends ``max_tokens`` and
``temperature=0``. A per-provider ledger survives across runs; a call is refused
when its estimated cost would push spend past the cap. A provider's own hard cap
(HTTP 402 / insufficient quota) also stops the run cleanly.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

import server.config  # noqa: F401  (loads .env into the environment)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODELS_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "models.json"
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"

Transport = Callable[[str, dict, dict], Awaitable[tuple[int, dict]]]


class BudgetExceeded(RuntimeError):
    """Raised before a call that would exceed the cap, or when the provider's credit runs out."""


class Budget:
    def __init__(self, cap_usd: float, ledger: Path) -> None:
        self.cap_usd = float(cap_usd)
        self.ledger = Path(ledger)
        self._lock = threading.Lock()
        self._spent = 0.0
        self._calls = 0
        if self.ledger.exists():
            try:
                data = json.loads(self.ledger.read_text(encoding="utf-8"))
                self._spent = float(data.get("spent_usd", 0.0))
                self._calls = int(data.get("calls", 0))
            except Exception:
                pass

    def spent(self) -> float:
        with self._lock:
            return self._spent

    def check(self, est_usd: float) -> None:
        with self._lock:
            if self._spent + max(0.0, est_usd) > self.cap_usd:
                raise BudgetExceeded(
                    f"budget cap ${self.cap_usd:.2f} reached (spent ${self._spent:.4f}, next ~${est_usd:.4f})"
                )

    def charge(self, usd: float) -> None:
        with self._lock:
            self._spent += max(0.0, float(usd))
            self._calls += 1
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            self.ledger.write_text(
                json.dumps({"spent_usd": round(self._spent, 6), "calls": self._calls}), encoding="utf-8"
            )


@dataclass
class Price:
    prompt: float  # USD per token
    completion: float
    context: int


class Pricing:
    def __init__(self, path: Path = MODELS_CACHE) -> None:
        self._prices: dict[str, Price] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8")).get("data", [])
            for model in data:
                p = model.get("pricing") or {}
                self._prices[model["id"]] = Price(
                    float(p.get("prompt") or 0), float(p.get("completion") or 0), int(model.get("context_length") or 0)
                )

    def get(self, model: str) -> Price:
        key = model.replace("openai:", "openai/", 1) if model.startswith("openai:") else model
        return self._prices.get(key, Price(10e-6, 40e-6, 200_000))  # unknown: assume expensive


@dataclass
class LLMResult:
    message: dict
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_s: float


async def _httpx_transport(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        try:
            body = response.json()
        except Exception:
            body = {"error": response.text[:500]}
        return response.status_code, body


def provider_of(model: str) -> str:
    return "openai" if model.startswith("openai:") else "openrouter"


class LLMClient:
    def __init__(
        self,
        model: str,
        budget: Budget,
        max_tokens: int = 1024,
        pricing: Optional[Pricing] = None,
        transport: Optional[Transport] = None,
        retries: int = 3,
    ) -> None:
        self.model = model
        self.budget = budget
        self.max_tokens = max_tokens
        self.pricing = pricing or Pricing()
        self.transport = transport or _httpx_transport
        self.retries = retries
        self.provider = provider_of(model)

    def context_window(self) -> int:
        return self.pricing.get(self.model).context

    def estimate_cost(self, prompt_chars: int) -> float:
        price = self.pricing.get(self.model)
        return (prompt_chars / 3.5) * price.prompt + self.max_tokens * price.completion

    def _request(self, system: str, messages: list[dict], tools: list[dict]) -> tuple[str, dict, dict]:
        full = [{"role": "system", "content": system}, *messages]
        if self.provider == "openai":
            key = os.getenv("OPENAI_API_KEY", "").strip()
            payload = {"model": self.model.split(":", 1)[1], "messages": full, "tools": tools,
                       "max_completion_tokens": self.max_tokens}
            return OPENAI_URL, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, payload
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        payload = {"model": self.model, "messages": full, "tools": tools, "max_tokens": self.max_tokens,
                   "temperature": 0, "usage": {"include": True}}
        return OPENROUTER_URL, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, payload

    async def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult:
        prompt_chars = len(system) + sum(len(json.dumps(m)) for m in messages)
        self.budget.check(self.estimate_cost(prompt_chars))
        url, headers, payload = self._request(system, messages, tools)
        delay = 2.0
        last_error = ""
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                status, body = await self.transport(url, headers, payload)
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                status, body = 0, {"error": str(exc)}
            latency = time.perf_counter() - started
            if status == 200 and body.get("choices"):
                usage = body.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                cost = usage.get("cost")
                if cost is None:
                    price = self.pricing.get(self.model)
                    cost = prompt_tokens * price.prompt + completion_tokens * price.completion
                self.budget.charge(float(cost))
                return LLMResult(body["choices"][0].get("message") or {}, prompt_tokens, completion_tokens,
                                 float(cost), latency)
            text = json.dumps(body)[:400]
            if status == 402 or "insufficient_quota" in text or "credits" in text.lower() and status in (402, 403):
                raise BudgetExceeded(f"{self.provider} credit exhausted: {text}")
            last_error = f"HTTP {status}: {text}"
            if status in (0, 408, 409, 429) or status >= 500:
                if attempt < self.retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
            raise RuntimeError(last_error)
        raise RuntimeError(last_error)  # pragma: no cover


def budget_for(model: str, cap_usd: Optional[float] = None) -> Budget:
    provider = provider_of(model)
    cap = cap_usd if cap_usd is not None else float(os.getenv("OPENPOKE_EVAL_BUDGET_USD", "35"))
    return Budget(cap, RUNS_DIR / f"ledger-{provider}.json")
