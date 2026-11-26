"""Coordinate execution agents and batch their results for the interaction agent."""

from __future__ import annotations

import asyncio
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .runtime import ExecutionAgentRuntime, ExecutionResult
from ...config import get_settings
from ...logging_config import logger
from ...services.execution import loops
from ...services.execution.registry import get_agent_registry


_agent_locks: dict[
    str,
    weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock],
] = {}
_agent_locks_guard = threading.Lock()


def _get_agent_lock(agent_name: str) -> asyncio.Lock:
    """Return the shared lock for an agent in the current event loop."""

    loop = asyncio.get_running_loop()
    with _agent_locks_guard:
        locks_by_loop = _agent_locks.setdefault(agent_name, weakref.WeakKeyDictionary())
        lock = locks_by_loop.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            locks_by_loop[loop] = lock
        return lock


def _abandon_if_running(agent_name: str) -> None:
    """After a timeout or a run that never started, the agent must not stay "working now"."""
    try:
        if get_settings().router_mode == "reference":
            loops.abandon_run(get_agent_registry(), agent_name)
    except Exception as exc:  # pragma: no cover - bookkeeping never fails the run
        logger.warning(f"[{agent_name}] could not clear running state: {exc}")


@dataclass
class PendingExecution:
    """Track a pending execution request."""

    request_id: str
    agent_name: str
    instructions: str
    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class _BatchState:
    """Collect results for a single interaction-agent turn."""

    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)
    pending: int = 0
    results: List[ExecutionResult] = field(default_factory=list)
    flush_generation: int = 0
    flush_task: Optional[asyncio.Task[None]] = None


class ExecutionBatchManager:
    """Run execution agents and deliver their combined outcome."""

    # Initialize batch manager with timeout and coordination state for execution agents
    def __init__(self, timeout_seconds: int = 90, coalesce_seconds: float = 1.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.coalesce_seconds = max(0.0, float(coalesce_seconds))
        self._pending: Dict[str, PendingExecution] = {}
        self._batch_lock = asyncio.Lock()
        self._batch_states: Dict[str, _BatchState] = {}

    # Run execution agent with timeout handling and batch coordination for interaction agent
    async def execute_agent(
        self,
        agent_name: str,
        instructions: str,
        request_id: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute an agent asynchronously and buffer the result for batch dispatch."""

        if not request_id:
            request_id = str(uuid.uuid4())

        batch_id = await self._register_pending_execution(agent_name, instructions, request_id, batch_id)

        try:
            agent_lock = _get_agent_lock(agent_name)
            async with agent_lock:
                try:
                    logger.info(f"[{agent_name}] Execution started")
                    runtime = ExecutionAgentRuntime(agent_name=agent_name)
                    result = await asyncio.wait_for(
                        runtime.execute(instructions),
                        timeout=self.timeout_seconds,
                    )
                    status = "SUCCESS" if result.success else "FAILED"
                    logger.info(f"[{agent_name}] Execution finished: {status}")
                except asyncio.TimeoutError:
                    logger.error(
                        f"[{agent_name}] Execution timed out after {self.timeout_seconds}s"
                    )
                    result = ExecutionResult(
                        agent_name=agent_name,
                        success=False,
                        response=f"Execution timed out after {self.timeout_seconds} seconds",
                        error="Timeout",
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception(f"[{agent_name}] Execution failed unexpectedly")
                    result = ExecutionResult(
                        agent_name=agent_name,
                        success=False,
                        response=f"Execution failed: {exc}",
                        error=str(exc),
                    )
        finally:
            self._pending.pop(request_id, None)

        if not result.success:
            _abandon_if_running(agent_name)

        await self._complete_execution(batch_id, result, agent_name)
        return result

    # Add execution request to current batch or create new batch if none exists
    async def _register_pending_execution(
        self,
        agent_name: str,
        instructions: str,
        request_id: str,
        batch_id: Optional[str],
    ) -> str:
        """Attach a new execution to its turn's batch, opening one when required."""

        batch_key = batch_id or f"single:{request_id}"
        async with self._batch_lock:
            state = self._batch_states.get(batch_key)
            if state is None:
                state = _BatchState(batch_id=batch_key)
                self._batch_states[batch_key] = state

            state.pending += 1
            self._pending[request_id] = PendingExecution(
                request_id=request_id,
                agent_name=agent_name,
                instructions=instructions,
                batch_id=batch_key,
            )

            return batch_key

    # Store execution result and send combined batch to interaction agent when complete
    async def _complete_execution(
        self,
        batch_id: str,
        result: ExecutionResult,
        agent_name: str,
    ) -> None:
        """Record the execution result and dispatch when the batch drains."""

        async with self._batch_lock:
            state = self._batch_states.get(batch_id)
            if state is None:
                logger.warning(f"[{agent_name}] Dropping result for unknown batch")
                return

            state.results.append(result)
            state.pending -= 1
            state.flush_generation += 1
            generation = state.flush_generation
            state.flush_task = asyncio.create_task(
                self._flush_batch_after_delay(batch_id, generation),
                name=f"execution-batch-flush-{batch_id}",
            )

    async def _flush_batch_after_delay(self, batch_id: str, generation: int) -> None:
        """Flush after a short quiet window, without waiting for still-running agents."""
        try:
            await asyncio.sleep(self.coalesce_seconds)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise

        dispatch_payload: Optional[str] = None
        async with self._batch_lock:
            state = self._batch_states.get(batch_id)
            if state is None or generation != state.flush_generation or not state.results:
                return

            results = list(state.results)
            state.results.clear()
            state.flush_task = None
            if state.pending <= 0:
                self._batch_states.pop(batch_id, None)
            dispatch_payload = self._format_batch_payload(results)
            agent_names = [entry.agent_name for entry in results]
            logger.info(f"Execution batch dispatching: {', '.join(agent_names)}")

        await self._dispatch_to_interaction_agent(dispatch_payload)

    # Return list of currently pending execution requests for monitoring purposes
    def get_pending_executions(self) -> List[Dict[str, str]]:
        """Expose pending executions for observability."""

        return [
            {
                "request_id": pending.request_id,
                "agent_name": pending.agent_name,
                "batch_id": pending.batch_id,
                "created_at": pending.created_at.isoformat(),
                "elapsed_seconds": (datetime.now() - pending.created_at).total_seconds(),
            }
            for pending in self._pending.values()
        ]

    # Clean up all pending executions and batch state on shutdown
    async def shutdown(self) -> None:
        """Clear pending bookkeeping and scheduled result flushes."""

        self._pending.clear()
        flush_tasks: List[asyncio.Task[None]] = []
        async with self._batch_lock:
            for state in self._batch_states.values():
                if state.flush_task is not None:
                    state.flush_task.cancel()
                    flush_tasks.append(state.flush_task)
            self._batch_states.clear()
        if flush_tasks:
            await asyncio.gather(*flush_tasks, return_exceptions=True)

    # Format multiple execution results into single message for interaction agent
    def _format_batch_payload(self, results: List[ExecutionResult]) -> str:
        """Render execution results into the interaction-agent format."""

        entries: List[str] = []
        for result in results:
            status = "SUCCESS" if result.success else "FAILED"
            response_text = (result.response or "(no response provided)").strip()
            entries.append(f"[{status}] {result.agent_name}: {response_text}")
        return "\n".join(entries)

    # Forward combined execution results to interaction agent for user response generation
    async def _dispatch_to_interaction_agent(self, payload: str) -> None:
        """Send the aggregated execution summary to the interaction agent."""

        from ..interaction_agent.runtime import InteractionAgentRuntime

        runtime = InteractionAgentRuntime()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(runtime.handle_agent_message(payload))
            return

        loop.create_task(runtime.handle_agent_message(payload))
