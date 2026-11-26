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


def test_missing_wrong_and_path_like_tenant_ids_fail_closed(tmp_path):
    enable(tmp_path)
    provision_tenant("alice")
    with pytest.raises(TenantError):
        scoped_data_dir(tmp_path)
    for bad in ("", "../alice", "alice/../bob", "alice bob", "x" * 65):
        with pytest.raises(TenantError):
            provision_tenant(bad)
    with pytest.raises(TenantError):
        tenant_context("bob").__enter__()


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
        assert client.get("/api/v1/meta/timezone").status_code == 401
        assert client.get("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "../bad"}).status_code == 400
        assert client.post("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "alice"}, json={"timezone": "America/Toronto"}).status_code == 200
        assert client.get("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "alice"}).json()["timezone"] == "America/Toronto"
        assert client.get("/api/v1/meta/timezone", headers={"X-Openpoke-Tenant": "bob"}).json()["timezone"] == "UTC"
        assert client.get("/api/v1/health").status_code == 200
