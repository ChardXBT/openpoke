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
from ...services.tenancy import current_tenant_id, multi_tenant_storage_enabled


_agent_locks: dict[
    str,
    weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock],
] = {}
_agent_locks_guard = threading.Lock()


def _batch_tenant_key() -> str:
    return current_tenant_id() if multi_tenant_storage_enabled() else "default"


def _get_agent_lock(agent_name: str) -> asyncio.Lock:
    """Return the shared lock for an agent in the current event loop and tenant."""

    loop = asyncio.get_running_loop()
    lock_name = f"{_batch_tenant_key()}\0{agent_name}"
    with _agent_locks_guard:
        locks_by_loop = _agent_locks.setdefault(lock_name, weakref.WeakKeyDictionary())
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


class ExecutionBatchManager:
    """Run execution agents and deliver their combined outcome."""

    # Initialize batch manager with timeout and coordination state for execution agents
    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: Dict[str, PendingExecution] = {}
        self._batch_lock = asyncio.Lock()
        self._batch_state: Optional[_BatchState] = None  # legacy single-tenant state
        self._batch_states: dict[str, _BatchState] = {}

    # Run execution agent with timeout handling and batch coordination for interaction agent
    async def execute_agent(
        self,
        agent_name: str,
        instructions: str,
        request_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute an agent asynchronously and buffer the result for batch dispatch."""

        if not request_id:
            request_id = str(uuid.uuid4())

        batch_id = await self._register_pending_execution(agent_name, instructions, request_id)

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
    ) -> str:
        """Attach a new execution to the active batch, opening one when required."""

        async with self._batch_lock:
            tenant_key = _batch_tenant_key()
            if multi_tenant_storage_enabled():
                state = self._batch_states.get(tenant_key)
                if state is None:
                    state = _BatchState(batch_id=str(uuid.uuid4()))
                    self._batch_states[tenant_key] = state
            else:
                if self._batch_state is None:
                    self._batch_state = _BatchState(batch_id=str(uuid.uuid4()))
                state = self._batch_state
            batch_id = state.batch_id

            state.pending += 1
            self._pending[request_id] = PendingExecution(
                request_id=request_id,
                agent_name=agent_name,
                instructions=instructions,
                batch_id=batch_id,
            )

            return batch_id

    # Store execution result and send combined batch to interaction agent when complete
    async def _complete_execution(
        self,
        batch_id: str,
        result: ExecutionResult,
        agent_name: str,
    ) -> None:
        """Record the execution result and dispatch when the batch drains."""

        dispatch_payload: Optional[str] = None

        async with self._batch_lock:
            tenant_key = _batch_tenant_key()
            state = (
                self._batch_states.get(tenant_key)
                if multi_tenant_storage_enabled()
                else self._batch_state
            )
            if state is None or state.batch_id != batch_id:
                logger.warning(f"[{agent_name}] Dropping result for unknown batch")
                return

            state.results.append(result)
            state.pending -= 1

            if state.pending == 0:
                dispatch_payload = self._format_batch_payload(state.results)
                agent_names = [entry.agent_name for entry in state.results]
                logger.info(f"Execution batch completed: {', '.join(agent_names)}")
                if multi_tenant_storage_enabled():
                    self._batch_states.pop(tenant_key, None)
                else:
                    self._batch_state = None

        if dispatch_payload:
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
        """Clear pending bookkeeping (no background work remains)."""

        self._pending.clear()
        async with self._batch_lock:
            self._batch_state = None
            self._batch_states.clear()

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
