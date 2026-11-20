"""Regression tests for the beyond-the-brief execution fixes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.agents.execution_agent import agent as agent_module
from server.agents.execution_agent import batch_manager as batch_manager_module
from server.agents.execution_agent.batch_manager import ExecutionBatchManager, ExecutionResult
from server.openrouter_client import client as openrouter_client
from server.services.execution.log_store import ExecutionAgentLogStore


async def _discard_dispatch(self: ExecutionBatchManager, payload: str) -> None:
    """Keep batch tests focused on execution instead of interaction-agent delivery."""


def test_same_agent_runs_serialize(monkeypatch) -> None:
    async def scenario() -> list[str]:
        timeline: list[str] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        class SlowRuntime:
            def __init__(self, agent_name: str) -> None:
                self.agent_name = agent_name

            async def execute(self, instructions: str) -> ExecutionResult:
                timeline.append(f"start:{instructions}")
                if instructions == "first":
                    first_started.set()
                    await release_first.wait()
                timeline.append(f"end:{instructions}")
                return ExecutionResult(self.agent_name, True, instructions)

        monkeypatch.setattr(batch_manager_module, "ExecutionAgentRuntime", SlowRuntime)
        manager = ExecutionBatchManager()

        first = asyncio.create_task(manager.execute_agent("shared-agent", "first"))
        await first_started.wait()
        second = asyncio.create_task(manager.execute_agent("shared-agent", "second"))
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, second)
        return timeline

    monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", _discard_dispatch)

    assert asyncio.run(scenario()) == [
        "start:first",
        "end:first",
        "start:second",
        "end:second",
    ]


def test_different_agents_overlap(monkeypatch) -> None:
    async def scenario() -> int:
        active = 0
        max_active = 0
        started: set[str] = set()
        both_started = asyncio.Event()

        class SlowRuntime:
            def __init__(self, agent_name: str) -> None:
                self.agent_name = agent_name

            async def execute(self, instructions: str) -> ExecutionResult:
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                started.add(self.agent_name)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=0.5)
                active -= 1
                return ExecutionResult(self.agent_name, True, instructions)

        monkeypatch.setattr(batch_manager_module, "ExecutionAgentRuntime", SlowRuntime)
        manager = ExecutionBatchManager()
        await asyncio.gather(
            manager.execute_agent("agent-one", "first"),
            manager.execute_agent("agent-two", "second"),
        )
        return max_active

    monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", _discard_dispatch)

    assert asyncio.run(scenario()) == 2


def test_lock_shared_across_managers(monkeypatch) -> None:
    async def scenario() -> list[str]:
        timeline: list[str] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        class SlowRuntime:
            def __init__(self, agent_name: str) -> None:
                self.agent_name = agent_name

            async def execute(self, instructions: str) -> ExecutionResult:
                timeline.append(f"start:{instructions}")
                if instructions == "first":
                    first_started.set()
                    await release_first.wait()
                timeline.append(f"end:{instructions}")
                return ExecutionResult(self.agent_name, True, instructions)

        monkeypatch.setattr(batch_manager_module, "ExecutionAgentRuntime", SlowRuntime)
        first_manager = ExecutionBatchManager()
        second_manager = ExecutionBatchManager()

        first = asyncio.create_task(first_manager.execute_agent("shared-agent", "first"))
        await first_started.wait()
        second = asyncio.create_task(second_manager.execute_agent("shared-agent", "second"))
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, second)
        return timeline

    monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", _discard_dispatch)

    expected = ["start:first", "end:first", "start:second", "end:second"]
    assert asyncio.run(scenario()) == expected
    assert asyncio.run(scenario()) == expected


def test_history_limit_default(monkeypatch, tmp_path) -> None:
    log_store = ExecutionAgentLogStore(tmp_path)
    monkeypatch.setattr(agent_module, "get_execution_agent_logs", lambda: log_store)
    monkeypatch.setattr(
        agent_module,
        "get_settings",
        lambda: SimpleNamespace(execution_history_limit=20),
        raising=False,
    )
    for index in range(25):
        log_store.record_request("history-agent", f"request-{index:02d}")

    prompt = agent_module.ExecutionAgent("history-agent").build_system_prompt_with_history()

    assert prompt.count("<agent_request ") == 20
    assert ">request-04</agent_request>" not in prompt
    assert ">request-05</agent_request>" in prompt
    assert ">request-24</agent_request>" in prompt


def test_history_limit_explicit_zero_means_all(monkeypatch, tmp_path) -> None:
    log_store = ExecutionAgentLogStore(tmp_path)
    monkeypatch.setattr(agent_module, "get_execution_agent_logs", lambda: log_store)
    monkeypatch.setattr(
        agent_module,
        "get_settings",
        lambda: SimpleNamespace(execution_history_limit=20),
        raising=False,
    )
    for index in range(25):
        log_store.record_request("history-agent", f"request-{index:02d}")

    prompt = agent_module.ExecutionAgent(
        "history-agent", conversation_limit=0
    ).build_system_prompt_with_history()

    assert prompt.count("<agent_request ") == 25
    assert ">request-00</agent_request>" in prompt
    assert ">request-24</agent_request>" in prompt


def test_max_tokens_sent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": []}

    async def fake_post(self, url, *, headers, json, timeout):
        captured.update(json)
        return FakeResponse()

    monkeypatch.setattr(openrouter_client, "_headers", lambda **kwargs: {})
    monkeypatch.setattr(openrouter_client.httpx.AsyncClient, "post", fake_post)

    asyncio.run(
        openrouter_client.request_chat_completion(
            model="offline-test-model",
            messages=[{"role": "user", "content": "hello"}],
            api_key="offline-test-key",
        )
    )

    assert captured["max_tokens"] == 2048
