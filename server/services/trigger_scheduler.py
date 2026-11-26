"""Background scheduler that watches trigger definitions and executes them."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Set

from ..agents.execution_agent.batch_manager import ExecutionBatchManager
from ..agents.execution_agent.runtime import ExecutionResult
from ..logging_config import logger
from .triggers import TriggerRecord, get_trigger_service
from .triggers.service import TriggerLeaseLostError


UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class TriggerScheduler:
    """Polls stored triggers and launches execution agents when due."""

    def __init__(
        self,
        poll_interval_seconds: float = 10.0,
        lease_seconds: int = 120,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = max(1, int(lease_seconds))
        self._heartbeat_interval_seconds = max(0.01, float(heartbeat_interval_seconds))
        self._service = get_trigger_service()
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._in_flight: Set[int] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._task and not self._task.done():
                return
            loop = asyncio.get_running_loop()
            self._running = True
            self._task = loop.create_task(self._run(), name="trigger-scheduler")
            logger.info("Trigger scheduler started", extra={"interval": self._poll_interval})

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
                logger.info("Trigger scheduler stopped")

    async def _run(self) -> None:
        try:
            while self._running:
                await self._poll_once()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Trigger scheduler loop crashed", extra={"error": str(exc)})

    async def _poll_once(self) -> None:
        now = _utc_now()
        due_triggers = self._service.claim_due_triggers(before=now, lease_seconds=self._lease_seconds)
        if not due_triggers:
            return

        for trigger in due_triggers:
            if trigger.id in self._in_flight:
                continue
            self._in_flight.add(trigger.id)
            asyncio.create_task(self._execute_trigger(trigger), name=f"trigger-{trigger.id}")

    async def _execute_trigger(self, trigger: TriggerRecord) -> None:
        execution_task: Optional[asyncio.Task[ExecutionResult]] = None
        heartbeat_task: Optional[asyncio.Task[bool]] = None
        try:
            fired_at = _utc_now()
            instructions = self._format_instructions(trigger, fired_at)
            running_trigger = self._service.prepare_for_execution(trigger, fired_at=fired_at)
            if not self._service.renew_lease(running_trigger, lease_seconds=self._lease_seconds):
                raise TriggerLeaseLostError(f"trigger {trigger.id} lease is no longer current")
            logger.info(
                "Dispatching trigger",
                extra={
                    "trigger_id": trigger.id,
                    "agent": trigger.agent_name,
                    "scheduled_for": trigger.next_trigger,
                },
            )
            execution_manager = ExecutionBatchManager()
            execution_task = asyncio.create_task(
                execution_manager.execute_agent(
                    trigger.agent_name,
                    instructions,
                ),
                name=f"trigger-execution-{trigger.id}",
            )
            heartbeat_task = asyncio.create_task(
                self._renew_lease_loop(running_trigger),
                name=f"trigger-lease-heartbeat-{trigger.id}",
            )
            done, _ = await asyncio.wait(
                {execution_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
                logger.warning(
                    "Trigger execution stopped after lease ownership was lost",
                    extra={"trigger_id": trigger.id, "agent": trigger.agent_name},
                )
                return
            result = await execution_task
            if result.success:
                self._handle_success(running_trigger, fired_at)
            else:
                error_text = result.error or result.response
                self._handle_failure(running_trigger, fired_at, error_text)
        except TriggerLeaseLostError:
            logger.warning(
                "Trigger execution was not started because its lease was stale",
                extra={"trigger_id": trigger.id, "agent": trigger.agent_name},
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._handle_failure(trigger, _utc_now(), str(exc))
            logger.exception(
                "Trigger execution failed unexpectedly",
                extra={"trigger_id": trigger.id, "agent": trigger.agent_name},
            )
        finally:
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            self._in_flight.discard(trigger.id)

    async def _renew_lease_loop(self, trigger: TriggerRecord) -> bool:
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                renewed = self._service.renew_lease(trigger, lease_seconds=self._lease_seconds)
            except Exception:  # pragma: no cover - defensive; fail closed below
                renewed = False
            if not renewed:
                return False

    def _handle_success(self, trigger: TriggerRecord, fired_at: datetime) -> None:
        logger.info(
            "Trigger completed",
            extra={"trigger_id": trigger.id, "agent": trigger.agent_name},
        )
        self._service.complete_execution(trigger)

    def _handle_failure(self, trigger: TriggerRecord, fired_at: datetime, error: str) -> None:
        logger.warning(
            "Trigger execution failed",
            extra={
                "trigger_id": trigger.id,
                "agent": trigger.agent_name,
                "error": error,
            },
        )
        self._service.fail_execution(trigger, error)

    def _format_instructions(self, trigger: TriggerRecord, fired_at: datetime) -> str:
        scheduled_for = trigger.next_trigger or _isoformat(fired_at)
        metadata_lines = [f"Trigger ID: {trigger.id}"]
        if trigger.recurrence_rule:
            metadata_lines.append(f"Recurrence: {trigger.recurrence_rule}")
        if trigger.timezone:
            metadata_lines.append(f"Timezone: {trigger.timezone}")
        if trigger.start_time:
            metadata_lines.append(f"Start Time (UTC): {trigger.start_time}")

        metadata = "\n".join(f"- {line}" for line in metadata_lines)
        return (
            f"Trigger fired at {_isoformat(fired_at)} (UTC).\n"
            f"Scheduled occurrence time: {scheduled_for}.\n\n"
            f"Metadata:\n{metadata}\n\n"
            f"Payload:\n{trigger.payload}"
        )


_scheduler_instance: Optional[TriggerScheduler] = None


def get_trigger_scheduler() -> TriggerScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TriggerScheduler()
    return _scheduler_instance


__all__ = ["TriggerScheduler", "get_trigger_scheduler"]
