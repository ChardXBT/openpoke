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


def test_anthropic_provider_translates_both_ways():
    import asyncio
    import json
    from types import SimpleNamespace as NS

    from server.openrouter_client.anthropic_provider import anthropic_chat_completion

    sent = {}

    class FakeMessages:
        async def create(self, **params):
            sent.update(params)
            return NS(id="msg_1", model=params["model"], stop_reason="tool_use",
                      usage=NS(input_tokens=120, output_tokens=30),
                      content=[NS(type="thinking", thinking=""), NS(type="text", text="On it."),
                               NS(type="tool_use", id="tu_2", name="send_message_to_agent",
                                  input={"agent_name": "#1", "instructions": "send"})])

    history = [
        {"role": "user", "content": "send it"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "tu_1", "type": "function", "function": {"name": "search_agents", "arguments": '{"query": "alice"}'}}]},
        {"role": "tool", "tool_call_id": "tu_1", "content": '{"results": []}'},
    ]
    tools = [{"type": "function", "function": {"name": "send_message_to_agent", "description": "d",
                                               "parameters": {"type": "object", "properties": {}}}}]
    out = asyncio.run(anthropic_chat_completion(api_key="k", model="claude-sonnet-5-5", messages=history,
                                                system="SYS", tools=tools, max_tokens=500,
                                                client=NS(messages=FakeMessages())))
    assert sent["system"] == "SYS" and sent["thinking"] == {"type": "between_tools"}
    assert sent["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
    assert sent["messages"][1]["content"][0] == {"type": "tool_use", "id": "tu_1", "name": "search_agents",
                                                 "input": {"query": "alice"}}
    assert sent["messages"][2]["content"][0]["type"] == "tool_result"
    assert "temperature" not in sent
    msg = out["choices"][0]["message"]
    assert msg["content"] == "On it." and msg["tool_calls"][0]["id"] == "tu_2"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"])["agent_name"] == "#1"
    assert out["usage"] == {"prompt_tokens": 120, "completion_tokens": 30}


def test_anthropic_provider_settings(monkeypatch):
    import importlib

    import server.config as config

    monkeypatch.setenv("OPENPOKE_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENPOKE_MODEL", raising=False)
    monkeypatch.delenv("OPENPOKE_MEMORY_MODEL", raising=False)
    importlib.reload(config)
    s = config.Settings()
    assert s.llm_provider == "anthropic" and s.llm_api_key == "sk-test"
    assert s.interaction_agent_model == "claude-sonnet-5-5" and s.memory_model == "claude-haiku-5-5"
    monkeypatch.delenv("OPENPOKE_PROVIDER")
    importlib.reload(config)
