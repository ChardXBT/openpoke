"""Prompt-cache measurement with provider-shaped fake responses."""

import asyncio
import json

import pytest

from evals.caching.run import measure, summarize
from evals.harness.llm import Budget, LLMClient, Pricing, Price


class _Pricing(Pricing):
    def __init__(self):
        self._prices = {"m": Price(1e-6, 2e-6, 100_000)}


def test_fake_cache_measure_reports_hit_and_miss_counts():
    summary, rows = asyncio.run(measure(4))
    assert summary == {
        "requests": 4,
        "cache_hit_requests": 3,
        "cache_miss_requests": 1,
        "cache_hit_rate": 0.75,
        "prompt_tokens": 4800,
        "cached_prompt_tokens": 3600,
        "uncached_prompt_tokens": 1200,
        "cache_creation_tokens": 1200,
    }
    assert len(rows) == 4 and all("message" not in row for row in rows)


def test_cache_measurement_rejects_zero_and_negative_turns():
    with pytest.raises(ValueError):
        asyncio.run(measure(0))
    with pytest.raises(ValueError):
        asyncio.run(measure(-2))


def test_openai_nested_cache_usage_is_parsed(tmp_path):
    async def transport(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {
            "prompt_tokens": 100, "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 80}, "cache_creation_tokens": 20,
        }}

    client = LLMClient("m", Budget(1.0, tmp_path / "l.json"), pricing=_Pricing(), transport=transport)
    res = asyncio.run(client.complete("s", [], []))
    assert (res.cached_prompt_tokens, res.cache_creation_tokens) == (80, 20)


def test_anthropic_style_cache_usage_is_parsed(tmp_path):
    async def transport(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {
            "input_tokens": 100, "output_tokens": 5,
            "cache_read_input_tokens": 75, "cache_creation_input_tokens": 25,
        }}

    client = LLMClient("m", Budget(1.0, tmp_path / "l.json"), pricing=_Pricing(), transport=transport)
    res = asyncio.run(client.complete("s", [], []))
    assert (res.prompt_tokens, res.completion_tokens, res.cached_prompt_tokens, res.cache_creation_tokens) == (200, 5, 75, 25)


def test_summarize_empty_and_missing_cache_fields():
    assert summarize([])["cache_hit_rate"] == 0.0


def test_malformed_cache_usage_shapes_fall_back_to_zero(tmp_path):
    async def transport(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {
            "prompt_tokens": 100, "completion_tokens": 5,
            "prompt_tokens_details": [], "cached_prompt_tokens": "bad", "cache_creation_tokens": None,
        }}

    client = LLMClient("m", Budget(1.0, tmp_path / "l.json"), pricing=_Pricing(), transport=transport)
    res = asyncio.run(client.complete("s", [], []))
    assert (res.cached_prompt_tokens, res.cache_creation_tokens) == (0, 0)


def test_anthropic_prompt_total_includes_cached_and_created_tokens(tmp_path):
    async def transport(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {
            "input_tokens": 100, "output_tokens": 5,
            "cache_read_input_tokens": 75, "cache_creation_input_tokens": 25,
        }}

    client = LLMClient("m", Budget(1.0, tmp_path / "l.json"), pricing=_Pricing(), transport=transport)
    res = asyncio.run(client.complete("s", [], []))
    assert res.prompt_tokens == 200
