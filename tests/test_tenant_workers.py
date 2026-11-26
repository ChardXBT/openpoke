import asyncio
import os

from server.services.tenancy import active_tenants, provision_tenant, retire_tenant, tenant_namespace
from server.services.tenant_workers import TenantWorkerManager


def setup_function():
    os.environ.pop("OPENPOKE_MULTI_TENANT", None)
    os.environ.pop("OPENPOKE_DATA_DIR", None)


def teardown_function():
    os.environ.pop("OPENPOKE_MULTI_TENANT", None)
    os.environ.pop("OPENPOKE_DATA_DIR", None)


def enable(tmp_path):
    os.environ["OPENPOKE_MULTI_TENANT"] = "1"
    os.environ["OPENPOKE_DATA_DIR"] = str(tmp_path)


class FakeService:
    instances = []

    def __init__(self, namespace, kind):
        self.namespace = namespace
        self.kind = kind
        self.started = False
        self.stopped = False
        self._task = None
        FakeService.instances.append(self)

    async def start(self):
        self.started = True

        async def idle():
            while True:
                await asyncio.sleep(60)

        self._task = asyncio.create_task(idle())

    async def stop(self):
        self.stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def factory_for(kind, calls=None):
    from server.services.tenancy import current_namespace

    def make():
        if calls is not None:
            calls.append((kind, current_namespace()))
        return FakeService(current_namespace(), kind)

    return make


def run(coro):
    return asyncio.run(coro)


def wait_for(predicate, timeout=2.0):
    async def poll():
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return predicate()

    return asyncio.run(poll())


def test_active_tenants_are_stable_and_do_not_expose_namespaces(tmp_path):
    enable(tmp_path)
    provision_tenant("zoe")
    provision_tenant("alice")
    assert active_tenants() == ["alice", "zoe"]
    assert all(tenant not in tenant_namespace(tenant) for tenant in active_tenants())


def test_workers_start_once_per_active_tenant_with_isolated_contexts(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    FakeService.instances = []
    calls = []

    async def exercise():
        manager = TenantWorkerManager(
            scan_interval_seconds=0.01,
            retry_interval_seconds=0.01,
            scheduler_factory=factory_for("triggers", calls),
            watcher_factory=factory_for("gmail", calls),
        )
        alice_ns = tenant_namespace("alice")
        bob_ns = tenant_namespace("bob")
        await manager.start()
        deadline = asyncio.get_running_loop().time() + 2
        while len(FakeService.instances) < 4 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.04)
        assert len(FakeService.instances) == 4
        assert len(calls) == 4
        namespaces = {service.namespace for service in FakeService.instances}
        assert namespaces == {tenant_namespace("alice"), tenant_namespace("bob")}
        assert {(service.namespace, service.kind) for service in FakeService.instances} == {
            (tenant_namespace("alice"), "triggers"),
            (tenant_namespace("alice"), "gmail"),
            (tenant_namespace("bob"), "triggers"),
            (tenant_namespace("bob"), "gmail"),
        }
        await manager.stop()

    run(exercise())
    assert all(service.stopped for service in FakeService.instances)


def test_retired_tenant_workers_stop_without_touching_other_tenants(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    FakeService.instances = []

    async def exercise():
        manager = TenantWorkerManager(
            scan_interval_seconds=0.01,
            retry_interval_seconds=0.01,
            scheduler_factory=factory_for("triggers"),
            watcher_factory=factory_for("gmail"),
        )
        alice_ns = tenant_namespace("alice")
        bob_ns = tenant_namespace("bob")
        await manager.start()
        deadline = asyncio.get_running_loop().time() + 2
        while len(FakeService.instances) < 4 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        retire_tenant("bob")
        await asyncio.sleep(0.05)
        bob_services = [s for s in FakeService.instances if s.namespace == bob_ns]
        alice_services = [s for s in FakeService.instances if s.namespace == alice_ns]
        assert bob_services and all(service.stopped for service in bob_services)
        assert alice_services and not any(service.stopped for service in alice_services)
        await manager.stop()

    run(exercise())


def test_one_tenant_factory_failure_retries_without_blocking_another_tenant(tmp_path):
    enable(tmp_path)
    provision_tenant("bad")
    provision_tenant("good")
    FakeService.instances = []
    attempts = {"bad": 0, "good": 0}

    def sometimes_failing_factory():
        from server.services.tenancy import current_namespace, current_tenant

        tenant = current_tenant()
        attempts[tenant] += 1
        if tenant == "bad" and attempts[tenant] == 1:
            raise RuntimeError("injected tenant-local failure")
        return FakeService(current_namespace(), "triggers")

    async def exercise():
        manager = TenantWorkerManager(
            scan_interval_seconds=0.01,
            retry_interval_seconds=0.01,
            scheduler_factory=sometimes_failing_factory,
            watcher_factory=factory_for("gmail"),
        )
        await manager.start()
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            good_started = any(s.namespace == tenant_namespace("good") and s.kind == "triggers" for s in FakeService.instances)
            bad_retried = attempts["bad"] >= 2
            if good_started and bad_retried:
                break
            await asyncio.sleep(0.01)
        assert attempts["bad"] >= 2
        assert any(s.namespace == tenant_namespace("good") and s.kind == "triggers" for s in FakeService.instances)
        assert any(s.namespace == tenant_namespace("bad") and s.kind == "triggers" for s in FakeService.instances)
        await manager.stop()

    run(exercise())
