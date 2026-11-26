import asyncio

import pytest

from evals.harness.llm import Budget, LLMClient, Pricing, Price


class CachePricing(Pricing):
    def __init__(self):
        self._prices = {"m": Price(1e-6, 2e-6, 100_000, cache_read=1e-7, cache_write=1.25e-6)}


def make_client(tmp_path, usage, pricing=None):
    async def transport(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": usage}

    return LLMClient("m", Budget(1.0, tmp_path / "ledger.json"), pricing=pricing or CachePricing(), transport=transport)


def test_openai_cached_tokens_are_priced_as_cache_reads(tmp_path):
    client = make_client(tmp_path, {
        "prompt_tokens": 100, "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 60},
    })
    res = asyncio.run(client.complete("s", [], []))
    assert res.prompt_tokens == 100
    assert res.cached_prompt_tokens == 60
    assert res.cache_creation_prompt_tokens == 0
    assert res.uncached_prompt_tokens == 40
    assert res.cost_usd == pytest.approx(40 * 1e-6 + 60 * 1e-7 + 10 * 2e-6)
    assert res.cache_cost_unknown is False


def test_anthropic_cache_read_and_creation_are_separate(tmp_path):
    client = make_client(tmp_path, {
        "prompt_tokens": 120, "completion_tokens": 10,
        "cache_read_input_tokens": 50, "cache_creation_input_tokens": 30,
    })
    res = asyncio.run(client.complete("s", [], []))
    assert res.uncached_prompt_tokens == 40
    assert res.cached_prompt_tokens == 50
    assert res.cache_creation_prompt_tokens == 30
    assert res.cost_usd == pytest.approx(40 * 1e-6 + 50 * 1e-7 + 30 * 1.25e-6 + 10 * 2e-6)


def test_unknown_cache_price_is_conservative_and_explicit(tmp_path):
    class NoCachePricing(Pricing):
        def __init__(self):
            self._prices = {"m": Price(1e-6, 2e-6, 100_000)}

    client = make_client(tmp_path, {
        "prompt_tokens": 100, "completion_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 90},
    }, NoCachePricing())
    res = asyncio.run(client.complete("s", [], []))
    assert res.cost_usd == pytest.approx(100 * 1e-6)
    assert res.cache_cost_unknown is True


def test_pricing_reads_openrouter_cache_price_fields(tmp_path):
    models = tmp_path / "models.json"
    models.write_text('{"data":[{"id":"m","context_length":1000,"pricing":{"prompt":"0.000001","completion":"0.000002","input_cache_read":"0.0000001","input_cache_write":"0.00000125"}}]}')
    price = Pricing(models).get("m")
    assert price.cache_read == pytest.approx(1e-7)
    assert price.cache_write == pytest.approx(1.25e-6)


def test_inconsistent_cache_totals_are_priced_uncached_and_flagged(tmp_path):
    client = make_client(tmp_path, {
        "prompt_tokens": 10, "completion_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 9}, "cache_creation_input_tokens": 8,
    })
    res = asyncio.run(client.complete("s", [], []))
    assert res.uncached_prompt_tokens == 10
    assert res.cached_prompt_tokens == 0 and res.cache_creation_prompt_tokens == 0
    assert res.cost_usd == pytest.approx(10 * 1e-6)
    assert res.cache_cost_unknown is True
