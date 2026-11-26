"""Tenant-aware supervisors for background trigger and Gmail workers."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from ..logging_config import logger
from .gmail.importance_watcher import ImportantEmailWatcher
from .tenancy import active_tenants, tenant_context
from .trigger_scheduler import TriggerScheduler

ServiceFactory = Callable[[], object]


class TenantWorkerManager:
    """Run isolated scheduler and watcher instances for every active tenant.

    Each service is constructed and started inside its tenant context. The asyncio
    tasks created by the service copy that context, so later polls, trigger
    executions and Gmail dispatches keep using the same tenant namespace even
    though one process hosts many tenants.
    """

    def __init__(
        self,
        *,
        scan_interval_seconds: float = 5.0,
        retry_interval_seconds: float = 1.0,
        scheduler_factory: Optional[ServiceFactory] = None,
        watcher_factory: Optional[ServiceFactory] = None,
    ) -> None:
        self._scan_interval = scan_interval_seconds
        self._retry_interval = retry_interval_seconds
        self._scheduler_factory = scheduler_factory or TriggerScheduler
        self._watcher_factory = watcher_factory or ImportantEmailWatcher
        self._task: Optional[asyncio.Task[None]] = None
        self._workers: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._task and not self._task.done():
                return
            self._running = True
            self._task = asyncio.create_task(self._run(), name="tenant-worker-manager")
            logger.info("Tenant worker manager started")

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            tasks = [self._task, *self._workers.values()]
            for task in tasks:
                if task and not task.done():
                    task.cancel()
            for task in tasks:
                if task:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._task = None
            self._workers.clear()
            logger.info("Tenant worker manager stopped")

    async def _run(self) -> None:
        try:
            while self._running:
                self._reconcile()
                await asyncio.sleep(self._scan_interval)
        except asyncio.CancelledError:
            raise

    def _reconcile(self) -> None:
        try:
            tenants = set(active_tenants())
        except Exception as exc:  # keep existing workers running; retry next scan
            logger.warning("Tenant worker scan failed", extra={"error": str(exc)})
            return

        for key, task in list(self._workers.items()):
            tenant, _kind = key
            if tenant not in tenants or task.done():
                if not task.done():
                    task.cancel()
                self._workers.pop(key, None)

        for tenant in sorted(tenants):
            for kind, factory in (
                ("triggers", self._scheduler_factory),
                ("gmail", self._watcher_factory),
            ):
                key = (tenant, kind)
                if key not in self._workers:
                    self._workers[key] = asyncio.create_task(
                        self._service_worker(tenant, kind, factory),
                        name=f"tenant-{kind}-worker",
                    )

    async def _service_worker(self, tenant: str, kind: str, factory: ServiceFactory) -> None:
        failures = 0
        while self._running:
            service = None
            try:
                with tenant_context(tenant):
                    service = factory()
                    start = getattr(service, "start")
                    await start()
                    task = getattr(service, "_task", None)
                    if task is None:
                        raise RuntimeError("service did not create its worker task")
                    await task
                    raise RuntimeError("service worker task exited unexpectedly")
            except asyncio.CancelledError:
                if service is not None:
                    try:
                        await getattr(service, "stop")()
                    except Exception as exc:  # pragma: no cover - defensive cleanup
                        logger.warning("Tenant worker cleanup failed", extra={"kind": kind, "error": str(exc)})
                raise
            except Exception as exc:
                failures += 1
                logger.exception(
                    "Tenant worker failed; retrying without affecting other tenants",
                    extra={"kind": kind, "tenant": tenant, "error": str(exc), "failure_count": failures},
                )
                if service is not None:
                    try:
                        await getattr(service, "stop")()
                    except Exception as stop_exc:  # pragma: no cover - defensive cleanup
                        logger.warning(
                            "Tenant worker stop after failure failed",
                            extra={"kind": kind, "tenant": tenant, "error": str(stop_exc)},
                        )
                await asyncio.sleep(min(30.0, self._retry_interval * failures))


_manager_instance: Optional[TenantWorkerManager] = None


def get_tenant_worker_manager() -> TenantWorkerManager:
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = TenantWorkerManager()
    return _manager_instance


__all__ = ["TenantWorkerManager", "get_tenant_worker_manager"]
