import asyncio
import json

import pytest

from server.config import Settings
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.registry import AgentRegistry, set_agent_registry


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server.agents.execution_agent.agent as agent_mod
    import server.agents.execution_agent.runtime as runtime_mod

    reg = AgentRegistry(tmp_path / "agents.db")
    previous = None
    set_agent_registry(reg)
    logs = ExecutionAgentLogStore(tmp_path / "logs")
    monkeypatch.setattr(agent_mod, "get_execution_agent_logs", lambda: logs)

    def settings(mode="reference"):
        return Settings(openrouter_api_key="test-key", router_mode=mode)

    yield reg, runtime_mod, settings, monkeypatch
    set_agent_registry(previous)


def _scripted(responses):
    calls = iter(responses)

    async def fake_completion(**_kwargs):
        return next(calls)

    return fake_completion


def _tool_call(name, args):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}}]}


FINAL = {"choices": [{"message": {"content": "Draft ready for Alice."}}]}


def test_hook_records_draft_state(env):
    reg, runtime_mod, settings, monkeypatch = env
    monkeypatch.setattr(runtime_mod, "get_settings", lambda: settings("reference"))
    monkeypatch.setattr(runtime_mod, "request_chat_completion", _scripted([
        _tool_call("gmail_create_draft", {"recipient_email": "alice@acme.com", "subject": "Q3 deck", "body": "hi"}),
        FINAL,
    ]))
    monkeypatch.setattr(runtime_mod.loops, "_default_trigger_lookup", lambda name: False)
    rt = runtime_mod.ExecutionAgentRuntime("Email to Alice")
    rt.tool_registry = {"gmail_create_draft": lambda **kw: {"successful": True, "data": {"id": "dr_1"}}}
    result = asyncio.run(rt.execute("Draft the Q3 deck email to Alice"))
    assert result.success
    rec = reg.find("Email to Alice")
    assert rec.state == "waiting_on_user"
    assert rec.request_count == 1
    assert ("draft", "dr_1") in {(r.kind, r.value) for r in reg.refs(rec.id)}


def test_legacy_mode_does_not_touch_registry(env):
    reg, runtime_mod, settings, monkeypatch = env
    monkeypatch.setattr(runtime_mod, "get_settings", lambda: settings("legacy"))
    monkeypatch.setattr(runtime_mod, "request_chat_completion", _scripted([FINAL]))
    rt = runtime_mod.ExecutionAgentRuntime("Email to Alice")
    asyncio.run(rt.execute("anything"))
    assert reg.find("Email to Alice") is None


def test_registry_failure_never_fails_the_agent(env):
    reg, runtime_mod, settings, monkeypatch = env
    monkeypatch.setattr(runtime_mod, "get_settings", lambda: settings("reference"))
    monkeypatch.setattr(runtime_mod, "request_chat_completion", _scripted([FINAL]))

    def boom(*_args, **_kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(runtime_mod.loops, "begin_run", boom)
    rt = runtime_mod.ExecutionAgentRuntime("Email to Alice")
    result = asyncio.run(rt.execute("anything"))
    assert result.success


def test_timed_out_run_is_not_left_running_forever(env):
    """The batch manager cancels a run on timeout; the agent must not stay "working now"
    (an open state that is never archived) after it."""
    reg, runtime_mod, settings, monkeypatch = env
    monkeypatch.setattr(runtime_mod, "get_settings", lambda: settings("reference"))

    async def hang(**_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(runtime_mod, "request_chat_completion", hang)
    monkeypatch.setattr(runtime_mod.loops, "_default_trigger_lookup", lambda name: False)

    async def main():
        rt = runtime_mod.ExecutionAgentRuntime("Flight search")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rt.execute("find flights"), timeout=0.05)

    asyncio.run(main())
    assert reg.find("Flight search").state == "failed"


def test_batch_manager_marks_unstartable_run_failed(env):
    """The IA marks the target "working now" before dispatch; if the runtime cannot even be
    built, the agent must not stay "working now"."""
    reg, runtime_mod, settings, monkeypatch = env
    import server.agents.execution_agent.batch_manager as bm

    rec, _ = reg.get_or_create("Flight search")
    reg.touch(rec.id, state="running")

    def broken(*_a, **_k):
        raise ValueError("OpenRouter API key not configured")

    monkeypatch.setattr(bm, "ExecutionAgentRuntime", broken)
    monkeypatch.setattr(bm, "get_settings", lambda: settings("reference"), raising=False)
    manager = bm.ExecutionBatchManager()
    monkeypatch.setattr(manager, "_dispatch_to_interaction_agent", lambda *a, **k: asyncio.sleep(0), raising=False)
    result = asyncio.run(manager.execute_agent("Flight search", "find flights"))
    assert not result.success
    assert reg.get(rec.id).state == "failed"
