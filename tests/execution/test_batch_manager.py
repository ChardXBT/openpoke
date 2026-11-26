"""Per-turn execution result batching."""

import asyncio

from server.agents.execution_agent import batch_manager as batch_manager_module
from server.agents.execution_agent.batch_manager import ExecutionBatchManager, ExecutionResult


def test_fast_result_dispatches_before_slow_result_same_turn(monkeypatch):
    async def scenario():
        dispatches: list[str] = []
        dispatched = asyncio.Event()

        class TimedRuntime:
            def __init__(self, agent_name: str):
                self.agent_name = agent_name

            async def execute(self, instructions: str):
                if self.agent_name == "fast-agent":
                    await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(20.0)
                return ExecutionResult(self.agent_name, True, f"{self.agent_name} finished")

        async def record_dispatch(self, payload: str):
            dispatches.append(payload)
            dispatched.set()

        monkeypatch.setattr(batch_manager_module, "ExecutionAgentRuntime", TimedRuntime)
        monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", record_dispatch)
        manager = ExecutionBatchManager(coalesce_seconds=0.05)

        fast = asyncio.create_task(manager.execute_agent("fast-agent", "lookup", batch_id="turn-1"))
        slow = asyncio.create_task(manager.execute_agent("slow-agent", "deep research", batch_id="turn-1"))
        await asyncio.wait_for(dispatched.wait(), timeout=3.0)

        assert dispatches == ["[SUCCESS] fast-agent: fast-agent finished"]
        assert fast.done()
        assert not slow.done()
        slow.cancel()
        try:
            await slow
        except asyncio.CancelledError:
            pass
        await manager.shutdown()

    asyncio.run(scenario())


def test_results_from_different_turns_are_not_merged(monkeypatch):
    async def scenario():
        dispatches: list[str] = []

        class QuickRuntime:
            def __init__(self, agent_name: str):
                self.agent_name = agent_name

            async def execute(self, instructions: str):
                await asyncio.sleep(0.01)
                return ExecutionResult(self.agent_name, True, instructions)

        async def record_dispatch(self, payload: str):
            dispatches.append(payload)

        monkeypatch.setattr(batch_manager_module, "ExecutionAgentRuntime", QuickRuntime)
        monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", record_dispatch)
        manager = ExecutionBatchManager(coalesce_seconds=0.05)

        await asyncio.gather(
            manager.execute_agent("agent-a", "first turn result", batch_id="turn-1"),
            manager.execute_agent("agent-b", "second turn result", batch_id="turn-2"),
        )
        await asyncio.sleep(0.1)
        return dispatches

    dispatches = asyncio.run(scenario())
    assert len(dispatches) == 2
    assert any("first turn result" in payload and "second turn result" not in payload for payload in dispatches)
    assert any("second turn result" in payload and "first turn result" not in payload for payload in dispatches)


def test_near_simultaneous_same_turn_results_are_coalesced(monkeypatch):
    async def scenario():
        dispatches: list[str] = []

        class QuickRuntime:
            def __init__(self, agent_name: str):
                self.agent_name = agent_name

            async def execute(self, instructions: str):
                await asyncio.sleep(0.01)
                return ExecutionResult(self.agent_name, True, instructions)

        async def record_dispatch(self, payload: str):
            dispatches.append(payload)

        monkeypatch.setattr(batch_manager_module, "ExecutionAgentRuntime", QuickRuntime)
        monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", record_dispatch)
        manager = ExecutionBatchManager(coalesce_seconds=0.08)

        await asyncio.gather(
            manager.execute_agent("agent-a", "alpha", batch_id="turn-1"),
            manager.execute_agent("agent-b", "beta", batch_id="turn-1"),
        )
        await asyncio.sleep(0.15)
        return dispatches

    dispatches = asyncio.run(scenario())
    assert len(dispatches) == 1
    assert "alpha" in dispatches[0] and "beta" in dispatches[0]


def test_dispatcher_scopes_execution_batch_to_current_interaction_turn(monkeypatch):
    async def scenario():
        from server.agents.interaction_agent import tools as interaction_tools
        from server.agents.interaction_agent.turn_context import TurnContext, current_turn

        calls = []

        class CaptureManager:
            async def execute_agent(self, agent_name, instructions, batch_id=None):
                calls.append((agent_name, batch_id))
                return ExecutionResult(agent_name, True, "done")

        monkeypatch.setattr(interaction_tools, "_EXECUTION_BATCH_MANAGER", CaptureManager())

        first = TurnContext()
        second = TurnContext()
        token = current_turn.set(first)
        try:
            interaction_tools._default_dispatch("agent-a", "work")
        finally:
            current_turn.reset(token)
        token = current_turn.set(second)
        try:
            interaction_tools._default_dispatch("agent-b", "work")
        finally:
            current_turn.reset(token)
        await asyncio.sleep(0)
        return calls, first, second

    calls, first, second = asyncio.run(scenario())
    assert calls == [("agent-a", first.turn_id), ("agent-b", second.turn_id)]
    assert first.turn_id != second.turn_id
