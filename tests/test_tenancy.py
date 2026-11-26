import asyncio
import os
import threading

import pytest

from server.services.execution.registry import AgentRegistry, get_agent_registry, set_agent_registry
from server.services.profile.store import ProfileStore, get_profile_store
from server.services.tenancy import (
    TenantError,
    current_namespace,
    provision_tenant,
    retire_tenant,
    scoped_data_dir,
    tenant_context,
)


def setup_function():
    os.environ.pop("OPENPOKE_MULTI_TENANT", None)
    os.environ.pop("OPENPOKE_DATA_DIR", None)


def teardown_function():
    os.environ.pop("OPENPOKE_MULTI_TENANT", None)
    os.environ.pop("OPENPOKE_DATA_DIR", None)


def enable(tmp_path):
    os.environ["OPENPOKE_MULTI_TENANT"] = "1"
    os.environ["OPENPOKE_DATA_DIR"] = str(tmp_path)


def test_missing_malformed_and_unknown_tenant_ids_fail_closed(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    with pytest.raises(TenantError):
        scoped_data_dir(tmp_path)
    for bad in ("", " ", " padded", "padded ", "bad\x00tenant", chr(0xD800), "x" * 129, None, 17):
        with pytest.raises(TenantError):
            provision_tenant(bad)
    with pytest.raises(TenantError):
        tenant_context("bob").__enter__()


def test_opaque_unicode_separator_and_space_ids_are_isolated(tmp_path):
    enable(tmp_path)
    tenants = ("tenant-alpha/../tenant-beta", "tenant-ünicode", "tenant with space")
    for index, tenant in enumerate(tenants):
        provision_tenant(tenant)
        with tenant_context(tenant):
            assert tenant not in current_namespace()
            ProfileStore(scoped_data_dir(tmp_path) / "profile.db").remember("fact", "secret", f"secret-{index}")
    for index, tenant in enumerate(tenants):
        with tenant_context(tenant):
            values = [fact.value for fact in ProfileStore(scoped_data_dir(tmp_path) / "profile.db").current()]
            assert values == [f"secret-{index}"]


def test_tenant_data_dirs_are_random_and_id_reuse_gets_a_fresh_namespace(tmp_path):
    enable(tmp_path)
    first = provision_tenant("alice")
    assert first == provision_tenant("alice")
    assert "alice" not in first
    with tenant_context("alice") as namespace:
        assert namespace == first
        assert scoped_data_dir(tmp_path) == tmp_path / "tenants" / first
    assert retire_tenant("alice")
    second = provision_tenant("alice")
    assert second != first


def test_profile_store_memory_is_isolated_for_concurrent_tenants(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    with tenant_context("alice"):
        a = ProfileStore(scoped_data_dir(tmp_path) / "profile.db")
        a.remember("person", "mom", "Alice Mom", "a@example.test")
    with tenant_context("bob"):
        b = ProfileStore(scoped_data_dir(tmp_path) / "profile.db")
        b.remember("person", "mom", "Bob Mom", "b@example.test")
        assert b.person("mom").email == "b@example.test"
        assert all(f.email != "a@example.test" for f in b.current())
    with tenant_context("alice"):
        again = ProfileStore(scoped_data_dir(tmp_path) / "profile.db")
        assert again.person("mom").email == "a@example.test"
        assert all(f.email != "b@example.test" for f in again.current())


def test_registry_contacts_and_ids_are_isolated(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    with tenant_context("alice"):
        ra = AgentRegistry(scoped_data_dir(tmp_path) / "agents.db")
        aa, _ = ra.get_or_create("Email Mom")
        ra.add_ref(aa.id, "contact", "alice@example.test")
    with tenant_context("bob"):
        rb = AgentRegistry(scoped_data_dir(tmp_path) / "agents.db")
        ab, _ = rb.get_or_create("Email Mom")
        rb.add_ref(ab.id, "contact", "bob@example.test")
        assert rb.owners("contact", "alice@example.test") == []
        local = rb.get(aa.id)
        assert local is not None and local.id == ab.id  # numeric ids are tenant-local and never global handles
        assert rb.owners("contact", "bob@example.test")[0].id == ab.id
    with tenant_context("alice"):
        ra2 = AgentRegistry(scoped_data_dir(tmp_path) / "agents.db")
        assert ra2.owners("contact", "alice@example.test")[0].id == aa.id
        assert ra2.owners("contact", "bob@example.test") == []


def test_singleton_factories_use_concurrent_tenant_contexts(tmp_path, monkeypatch):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    set_agent_registry(None)
    monkeypatch.setattr("server.services.profile.store._store", None)

    async def write_profile(tenant, value):
        with tenant_context(tenant):
            get_profile_store().remember("fact", "city", value)
            await asyncio.sleep(0)
            return get_profile_store().person("city")

    async def run():
        return await asyncio.gather(write_profile("alice", "Toronto"), write_profile("bob", "Lisbon"))

    a, b = asyncio.run(run())
    assert a is None  # 'city' is a fact, not a person; absence must also stay tenant-local
    assert b is None
    with tenant_context("alice"):
        assert any(f.value == "Toronto" for f in get_profile_store().current())
        assert all(f.value != "Lisbon" for f in get_profile_store().current())
    with tenant_context("bob"):
        assert any(f.value == "Lisbon" for f in get_profile_store().current())
        assert all(f.value != "Toronto" for f in get_profile_store().current())


def test_trigger_reminders_are_isolated(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.triggers import get_trigger_service
    with tenant_context("alice"):
        service = get_trigger_service()
        reminder = service.create_trigger(agent_name="agent", payload="alice reminder")
    with tenant_context("bob"):
        other = get_trigger_service()
        assert other.list_triggers(agent_name="agent") == []
        assert other.get_due_triggers(before=reminder.next_trigger and __import__('datetime').datetime.max.replace(tzinfo=__import__('datetime').timezone.utc)) == []
        created = other.create_trigger(agent_name="agent", payload="bob reminder")
        assert created.id == reminder.id  # ids are local to each tenant database
        assert other.list_triggers(agent_name="agent")[0].payload == "bob reminder"
    with tenant_context("alice"):
        assert get_trigger_service().list_triggers(agent_name="agent")[0].payload == "alice reminder"


def test_conversation_memory_files_are_isolated(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.conversation import get_conversation_log, get_working_memory_log
    with tenant_context("alice"):
        get_conversation_log().record_user_message("alice secret memory")
    with tenant_context("bob"):
        transcript = get_conversation_log().load_transcript()
        working = get_working_memory_log().render_transcript()
        assert "alice secret memory" not in transcript
        assert "alice secret memory" not in working
        get_conversation_log().record_user_message("bob memory")
    with tenant_context("alice"):
        assert "alice secret memory" in get_conversation_log().load_transcript()
        assert "bob memory" not in get_conversation_log().load_transcript()


def test_reused_tenant_id_cannot_read_retired_memory(tmp_path):
    enable(tmp_path)
    provision_tenant("reuse")
    with tenant_context("reuse"):
        store = ProfileStore(scoped_data_dir(tmp_path) / "profile.db")
        store.remember("fact", "secret", "old tenant memory")
    retire_tenant("reuse")
    provision_tenant("reuse")
    with tenant_context("reuse"):
        fresh = ProfileStore(scoped_data_dir(tmp_path) / "profile.db")
        assert fresh.current() == []


def test_inflight_tenant_context_keeps_one_namespace_across_retirement(tmp_path):
    enable(tmp_path)
    first = provision_tenant("reuse")
    with tenant_context("reuse"):
        assert retire_tenant("reuse")
        assert scoped_data_dir(tmp_path) == tmp_path / "tenants" / first
        provision_tenant("reuse")
        assert scoped_data_dir(tmp_path) == tmp_path / "tenants" / first
    with tenant_context("reuse") as second:
        assert second != first


def test_gmail_active_account_is_scoped_and_fails_closed_without_tenant(tmp_path, monkeypatch):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.gmail import client as gmail_client
    monkeypatch.setattr(gmail_client, "_demo_mode", lambda: False)
    assert gmail_client.get_active_gmail_user_id() is None
    with tenant_context("alice"):
        gmail_client._set_active_gmail_user_id("gmail-a")
        assert gmail_client.get_active_gmail_user_id() == "gmail-a"
    with tenant_context("bob"):
        assert gmail_client.get_active_gmail_user_id() is None
        gmail_client._set_active_gmail_user_id("gmail-b")
    with tenant_context("alice"):
        assert gmail_client.get_active_gmail_user_id() == "gmail-a"
    assert gmail_client.get_active_gmail_user_id() is None


def test_demo_mailbox_and_execution_logs_are_isolated(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.gmail.demo_mailbox import get_demo_mailbox
    from server.services.execution import get_execution_agent_logs
    with tenant_context("alice"):
        get_demo_mailbox().inject("a@example.test", "Alice mail", "alice body")
        get_execution_agent_logs().record_request("agent", "alice execution memory")
    with tenant_context("bob"):
        bob_box = get_demo_mailbox()
        assert "alice body" not in str(bob_box.messages())
        assert "a@example.test" not in str(bob_box.execute("GMAIL_GET_CONTACTS", {}))
        assert "alice execution memory" not in get_execution_agent_logs().load_transcript("agent")
        get_demo_mailbox().inject("b@example.test", "Bob mail", "bob body")
    with tenant_context("alice"):
        assert "alice body" in str(get_demo_mailbox().messages())
        assert "bob body" not in str(get_demo_mailbox().messages())
        assert "alice execution memory" in get_execution_agent_logs().load_transcript("agent")


def test_timezone_store_is_isolated(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.timezone_store import get_timezone_store
    with tenant_context("alice"):
        get_timezone_store().set_timezone("America/Toronto")
    with tenant_context("bob"):
        assert get_timezone_store().get_timezone() == "UTC"
        get_timezone_store().set_timezone("Europe/Lisbon")
    with tenant_context("alice"):
        assert get_timezone_store().get_timezone() == "America/Toronto"


def test_api_middleware_requires_valid_tenant_and_scopes_requests(tmp_path):
    enable(tmp_path)
    from fastapi.testclient import TestClient
    from server.app import app
    with TestClient(app) as client:
        assert client.get("/api/v1/meta/timezone").status_code == 400
        assert client.get("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "bad\x00tenant"}).status_code == 400
        assert client.post("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "alice"}, json={"timezone": "America/Toronto"}).status_code == 200
        assert client.get("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "alice"}).json()["timezone"] == "America/Toronto"
        assert client.get("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "bob"}).json()["timezone"] == "UTC"
        assert client.get("/api/v1/health").status_code == 200


def test_cross_tenant_search_never_returns_foreign_agents_or_refs(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.execution.search import search_agents

    with tenant_context("alice"):
        registry = get_agent_registry()
        agent, _ = registry.get_or_create("Email Alice Client", topic="alpha porcelain invoice")
        registry.add_ref(agent.id, "contact", "alice-client@example.test")
    with tenant_context("bob"):
        foreign = get_agent_registry()
        assert foreign.owners("contact", "alice-client@example.test") == []
        assert search_agents(foreign, "alpha porcelain invoice") == []
        assert search_agents(foreign, "alice-client@example.test") == []
        local, _ = foreign.get_or_create("Email Alice Client", topic="beta porcelain invoice")
        assert local.id == agent.id
    with tenant_context("alice"):
        hits = search_agents(get_agent_registry(), "alice-client@example.test")
        assert [hit.agent.id for hit in hits] == [agent.id]


def test_gmail_seen_state_is_isolated_and_bounded_per_tenant(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.services.gmail.seen_store import get_gmail_seen_store

    with tenant_context("alice"):
        alice_seen = get_gmail_seen_store(max_entries=2)
        alice_seen.mark_seen(["", None, "alice-mail-1"])
        alice_seen.mark_seen(["alice-mail-1", "alice-mail-2"])
        alice_seen.mark_seen(["alice-mail-3"])
        assert alice_seen.snapshot() == ["alice-mail-2", "alice-mail-3"]
        assert alice_seen.is_seen("alice-mail-2")
    with tenant_context("bob"):
        bob_seen = get_gmail_seen_store(max_entries=2)
        assert bob_seen.snapshot() == []
        assert not bob_seen.is_seen("alice-mail-2")
        bob_seen.mark_seen(["bob-mail-1"])
    with tenant_context("alice"):
        assert not get_gmail_seen_store(max_entries=2).is_seen("bob-mail-1")
        assert get_gmail_seen_store(max_entries=2).is_seen("alice-mail-2")


def test_execution_batches_and_agent_locks_are_tenant_scoped(tmp_path, monkeypatch):
    enable(tmp_path)
    provision_tenant("alice")
    provision_tenant("bob")
    from server.agents.execution_agent.batch_manager import ExecutionBatchManager, ExecutionResult, _get_agent_lock

    async def run():
        manager = ExecutionBatchManager()
        sent = []

        async def dispatch(payload):
            sent.append(payload)

        monkeypatch.setattr(manager, "_dispatch_to_interaction_agent", dispatch)
        with tenant_context("alice"):
            alice_batch = await manager._register_pending_execution("shared-agent", "alice work", "alice-request")
            alice_lock = _get_agent_lock("shared-agent")
        with tenant_context("bob"):
            bob_batch = await manager._register_pending_execution("shared-agent", "bob work", "bob-request")
            bob_lock = _get_agent_lock("shared-agent")
        assert alice_batch != bob_batch
        assert alice_lock is not bob_lock
        with tenant_context("alice"):
            assert _get_agent_lock("shared-agent") is alice_lock
            await manager._complete_execution(alice_batch, ExecutionResult("shared-agent", True, "alpha done"), "shared-agent")
        with tenant_context("bob"):
            await manager._complete_execution(bob_batch, ExecutionResult("shared-agent", True, "beta done"), "shared-agent")
        return sent

    sent = asyncio.run(run())
    assert sent == ["[SUCCESS] shared-agent: alpha done", "[SUCCESS] shared-agent: beta done"]


def test_tenant_named_default_uses_a_tenant_namespace_not_legacy_files(tmp_path):
    enable(tmp_path)
    provision_tenant("default")
    with tenant_context("default"):
        get_profile_store().remember("fact", "private", "tenant default only")
        assert (scoped_data_dir(tmp_path) / "profile.db").exists()
    assert not (tmp_path / "profile.db").exists()


def test_corrupt_tenant_registry_fails_closed(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    registry = tmp_path / "tenants" / "registry.json"
    registry.write_text("not json", encoding="utf-8")
    with pytest.raises(TenantError):
        provision_tenant("alice")
    with pytest.raises(TenantError):
        tenant_context("alice").__enter__()


def test_valid_json_registry_with_malformed_namespace_fails_closed(tmp_path):
    enable(tmp_path)
    registry = tmp_path / "tenants" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"active":{"alice":"../escape"},"retired":{}}', encoding="utf-8")
    with pytest.raises(TenantError):
        tenant_context("alice").__enter__()


def test_valid_json_registry_with_malformed_tenant_key_fails_closed(tmp_path):
    enable(tmp_path)
    registry = tmp_path / "tenants" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"active":{"bad\\u0000tenant":"0123456789abcdef0123456789abcdef"},"retired":{}}', encoding="utf-8")
    with pytest.raises(TenantError):
        provision_tenant("alice")


def test_registry_active_retired_overlap_fails_closed(tmp_path):
    enable(tmp_path)
    registry = tmp_path / "tenants" / "registry.json"
    registry.parent.mkdir(parents=True)
    namespace = "0123456789abcdef0123456789abcdef"
    registry.write_text(
        '{"active":{"alice":"' + namespace + '"},"retired":{"alice":"' + namespace + '"}}',
        encoding="utf-8",
    )
    with pytest.raises(TenantError):
        provision_tenant("alice")


def test_concurrent_provisioning_of_same_tenant_returns_one_namespace(tmp_path):
    enable(tmp_path)
    results = []
    errors = []

    def provision():
        try:
            results.append(provision_tenant("alice"))
        except Exception as exc:  # pragma: no cover - assertion path below
            errors.append(exc)

    threads = [threading.Thread(target=provision) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    assert len(results) == 12
    assert len(set(results)) == 1
