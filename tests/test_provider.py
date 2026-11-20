import asyncio

from server.config import Settings


def test_provider_defaults(monkeypatch):
    for var in ("OPENPOKE_PROVIDER", "OPENPOKE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(openrouter_api_key="or-key", openai_api_key="oa-key")
    assert s.llm_provider == "openrouter" and s.llm_api_key == "or-key"
    assert s.interaction_agent_model == "anthropic/claude-sonnet-5.5"

    monkeypatch.setenv("OPENPOKE_PROVIDER", "openai")
    s = Settings(openrouter_api_key="or-key", openai_api_key="oa-key")
    assert s.llm_provider == "openai" and s.llm_api_key == "oa-key"
    assert s.interaction_agent_model == "gpt-6.1-terra"

    monkeypatch.setenv("OPENPOKE_PROVIDER", "bogus")
    assert Settings().llm_provider == "openrouter"


def _capture(monkeypatch, provider):
    import server.openrouter_client.client as client_mod

    seen = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    async def fake_post(self, url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json)
        return FakeResponse()

    monkeypatch.setattr(client_mod.httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(client_mod, "get_settings", lambda: Settings(
        openrouter_api_key="or-key", openai_api_key="oa-key", llm_provider=provider))
    return client_mod, seen


def test_openai_provider_request(monkeypatch):
    client_mod, seen = _capture(monkeypatch, "openai")
    asyncio.run(client_mod.request_chat_completion(model="openai/gpt-6.1-terra", messages=[], system="s",
                                                   tools=[{"type": "function"}], max_tokens=99))
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer oa-key"
    assert seen["payload"]["model"] == "gpt-6.1-terra"
    assert seen["payload"]["max_completion_tokens"] == 99 and "max_tokens" not in seen["payload"]
    assert seen["payload"]["reasoning_effort"] == "none"


def test_openrouter_provider_request(monkeypatch):
    client_mod, seen = _capture(monkeypatch, "openrouter")
    asyncio.run(client_mod.request_chat_completion(model="anthropic/claude-sonnet-5.5", messages=[], max_tokens=50))
    assert seen["url"].startswith("https://openrouter.ai/")
    assert seen["headers"]["Authorization"] == "Bearer or-key"
    assert seen["payload"]["max_tokens"] == 50
