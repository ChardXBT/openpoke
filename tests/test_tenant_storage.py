import threading

import pytest

from server.services.execution.registry import get_agent_registry
from server.services.gmail.seen_store import get_gmail_seen_store
from server.services.profile.store import get_profile_store
from server.services.tenancy import TenantResolutionError, use_tenant
from server.services.triggers import get_trigger_service
from server.services.conversation.log import get_conversation_log
from server.services.execution.log_store import get_execution_agent_logs


@pytest.fixture()
def tenant_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENPOKE_MULTI_TENANT_STORAGE", "1")
    import server.services.execution.registry as registry_module
    import server.services.gmail.seen_store as seen_module
    import server.services.profile.store as profile_module
    import server.services.triggers as triggers_module
    import server.services.conversation.log as conversation_module
    import server.services.conversation.summarization.working_memory_log as working_module
    import server.services.execution.log_store as execution_log_module

    monkeypatch.setattr(profile_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(registry_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(triggers_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(seen_module, "_DEFAULT_BASE_DIR", tmp_path)
    monkeypatch.setattr(conversation_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(working_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(execution_log_module, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(profile_module, "_store_cache", {})
    monkeypatch.setattr(registry_module, "_registry_cache", {})
    monkeypatch.setattr(triggers_module, "_trigger_cache", {})
    monkeypatch.setattr(seen_module, "_seen_cache", {})
    monkeypatch.setattr(conversation_module, "_conversation_cache", {})
    monkeypatch.setattr(working_module, "_working_memory_cache", {})
    monkeypatch.setattr(execution_log_module, "_execution_log_cache", {})
    return tmp_path


def test_missing_tenant_fails_closed(tenant_env):
    with pytest.raises(TenantResolutionError, match="tenant id is required"):
        get_profile_store()
    with pytest.raises(TenantResolutionError, match="tenant id is required"):
        get_agent_registry()
    with pytest.raises(TenantResolutionError, match="tenant id is required"):
        get_trigger_service()
    with pytest.raises(TenantResolutionError, match="tenant id is required"):
        get_gmail_seen_store()


def test_tenants_cannot_read_each_others_memory_contacts_mail_or_reminders(tenant_env):
    with use_tenant("tenant-alpha"):
        profile = get_profile_store()
        assert profile.remember("person", "sister", "Sarah Kim", email="sarah@example.test") == "add"
        registry = get_agent_registry()
        alpha_agent, _ = registry.get_or_create("Email Sarah", topic="alpha private draft")
        registry.add_ref(alpha_agent.id, "contact", "sarah@example.test")
        trigger = get_trigger_service().create_trigger(
            agent_name="Email Sarah", payload="alpha private reminder"
        )
        seen = get_gmail_seen_store()
        seen.mark_seen(["alpha-mail-1"])

    with use_tenant("tenant-beta"):
        assert get_profile_store().current() == []
        beta_registry = get_agent_registry()
        assert beta_registry.owners("contact", "sarah@example.test") == []
        beta_agent, _ = beta_registry.get_or_create("Email Sarah", topic="beta private draft")
        beta_registry.add_ref(beta_agent.id, "contact", "beta@example.test")
        assert get_trigger_service().list_triggers(agent_name="Email Sarah") == []
        beta_trigger = get_trigger_service().create_trigger(
            agent_name="Email Sarah", payload="beta private reminder"
        )
        assert get_gmail_seen_store().snapshot() == []

    # Local ids may be reused by SQLite in separate tenant databases; reuse must
    # resolve within the current tenant only, never across the boundary.
    assert alpha_agent.id == beta_agent.id
    assert trigger.id == beta_trigger.id
    with use_tenant("tenant-alpha"):
        assert get_agent_registry().get(alpha_agent.id).topic == "alpha private draft"
        assert get_trigger_service().list_triggers(agent_name="Email Sarah")[0].payload == "alpha private reminder"
        assert get_gmail_seen_store().snapshot() == ["alpha-mail-1"]
    with use_tenant("tenant-beta"):
        assert get_agent_registry().get(beta_agent.id).topic == "beta private draft"
        assert get_trigger_service().list_triggers(agent_name="Email Sarah")[0].payload == "beta private reminder"
        assert get_gmail_seen_store().snapshot() == []


def test_wrong_separator_unicode_and_reused_ids_never_escape_their_namespace(tenant_env):
    with use_tenant("tenant-alpha"):
        get_profile_store().remember("fact", "secret", "alpha only")
    for wrong in ("tenant-alpha/../tenant-beta", "tenant-alpha.evil", "tenant-ünicode"):
        with use_tenant(wrong):
            assert get_profile_store().current() == []
    # A reused tenant id is the same tenant, not a fresh empty one. Hosting must
    # therefore use non-reassignable identity ids; storage proves there is no
    # accidental fallback to another namespace when the same id is presented again.
    with use_tenant("tenant-alpha"):
        assert [fact.value for fact in get_profile_store().current()] == ["alpha only"]


def test_concurrent_tenants_keep_writes_separate(tenant_env):
    barrier = threading.Barrier(2)
    errors = []

    def write(tenant, value):
        try:
            with use_tenant(tenant):
                barrier.wait(timeout=5)
                get_profile_store().remember("fact", "private", value)
                get_gmail_seen_store().mark_seen([value])
                get_trigger_service().create_trigger(agent_name=value, payload=value)
        except Exception as exc:  # pragma: no cover - assertion path below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=("tenant-a", "alpha")) ,
               threading.Thread(target=write, args=("tenant-b", "beta"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    with use_tenant("tenant-a"):
        assert [fact.value for fact in get_profile_store().current()] == ["alpha"]
        assert get_gmail_seen_store().snapshot() == ["alpha"]
        assert [t.payload for t in get_trigger_service().list_triggers(agent_name="alpha")] == ["alpha"]
    with use_tenant("tenant-b"):
        assert [fact.value for fact in get_profile_store().current()] == ["beta"]
        assert get_gmail_seen_store().snapshot() == ["beta"]
        assert [t.payload for t in get_trigger_service().list_triggers(agent_name="beta")] == ["beta"]


def test_malformed_tenant_ids_fail_closed(tenant_env):
    for bad in ("", " ", "x" * 129, "bad\x00tenant", None):
        with pytest.raises(TenantResolutionError):
            with use_tenant(bad):
                pass


def test_tenant_named_default_does_not_share_the_legacy_store(tenant_env, monkeypatch):
    with use_tenant("default"):
        get_profile_store().remember("fact", "private", "tenant default only")
    monkeypatch.setenv("OPENPOKE_MULTI_TENANT_STORAGE", "0")
    assert get_profile_store().current() == []
    monkeypatch.setenv("OPENPOKE_MULTI_TENANT_STORAGE", "1")
    with use_tenant("default"):
        assert [fact.value for fact in get_profile_store().current()] == ["tenant default only"]



def test_conversation_and_execution_journals_are_tenant_local(tenant_env):
    with use_tenant("tenant-alpha"):
        get_conversation_log().record_user_message("alpha private mail plan")
        get_execution_agent_logs().record_request("alpha-agent", "alpha private instructions")
    with use_tenant("tenant-beta"):
        assert get_conversation_log().load_transcript() == ""
        assert get_execution_agent_logs().list_agents() == []
        get_conversation_log().record_user_message("beta private mail plan")
    with use_tenant("tenant-alpha"):
        assert "alpha private mail plan" in get_conversation_log().load_transcript()
        assert "beta private" not in get_conversation_log().load_transcript()
        assert get_execution_agent_logs().list_agents() == ["alpha-agent"]


def test_http_requests_require_and_bind_a_tenant_when_enabled(monkeypatch):
    monkeypatch.setenv("OPENPOKE_MULTI_TENANT_STORAGE", "1")
    from fastapi.testclient import TestClient
    from server.app import app

    with TestClient(app) as client:
        missing = client.get("/api/v1/meta/timezone")
        assert missing.status_code == 400
        assert "tenant id is required" in missing.json()["error"]
        bound = client.get("/api/v1/meta/timezone", headers={"X-OpenPoke-Tenant": "tenant-alpha"})
        assert bound.status_code == 200
