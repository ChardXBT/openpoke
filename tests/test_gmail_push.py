import asyncio
import base64
import json
from datetime import datetime, timezone

import pytest

from server.services.execution.registry import AgentRegistry
from server.services.gmail.push import GmailApiError, GmailPushService, PushStateStore

ME = "owner@example.com"


def envelope(history_id, email=ME):
    raw = json.dumps({"emailAddress": email, "historyId": str(history_id)}).encode()
    return {"message": {"data": base64.urlsafe_b64encode(raw).decode(), "messageId": "pubsub-1"},
            "subscription": "projects/p/subscriptions/openpoke"}


class FakeGateway:
    def __init__(self):
        self.watch_calls = []
        self.history_calls = []
        self.message_calls = []
        self.pages = []
        self.messages = {}
        self.history_error = None
        self.message_errors = {}
        self.watch_response = {"historyId": "100", "expiration": "1893456000000"}

    def get_profile(self):
        return {"emailAddress": ME}

    def watch(self, topic_name, label_ids):
        self.watch_calls.append((topic_name, list(label_ids)))
        return dict(self.watch_response)

    def list_history(self, start_history_id, page_token=None):
        self.history_calls.append((str(start_history_id), page_token))
        if self.history_error:
            raise self.history_error
        if not self.pages:
            return {"history": [], "historyId": str(start_history_id)}
        return self.pages.pop(0)

    def get_message(self, message_id):
        self.message_calls.append(message_id)
        if message_id in self.message_errors:
            raise self.message_errors[message_id]
        return self.messages[message_id]


def api_message(message_id="m1", thread_id="thr_1", sender="Alice <alice@example.com>",
                subject="Re: Lunch", body="Thursday works", labels=None, internal_date="1763931600000"):
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": labels if labels is not None else ["INBOX"],
        "internalDate": internal_date,
        "snippet": body,
        "payload": {"headers": [
            {"name": "From", "value": sender},
            {"name": "To", "value": ME},
            {"name": "Subject", "value": subject},
        ], "body": {"data": ""}},
    }


def added(message_id, thread_id, labels=None, history_id="20"):
    msg = {"id": message_id, "threadId": thread_id, "labelIds": labels if labels is not None else ["INBOX"]}
    return {"id": str(history_id), "messagesAdded": [{"message": msg}]}


def make_service(tmp_path, gateway=None, dispatcher=None, registry=None, seen=None):
    gateway = gateway or FakeGateway()
    dispatched = []
    async def default_dispatch(summary, email):
        dispatched.append((summary, email))
    service = GmailPushService(
        gateway,
        PushStateStore(tmp_path / "push-state.json"),
        dispatcher=dispatcher or default_dispatch,
        registry=registry,
        seen_store=seen,
    )
    return service, gateway, dispatched


def owned_registry(tmp_path, thread_id="thr_1"):
    reg = AgentRegistry(tmp_path / "agents.db")
    rec, _ = reg.get_or_create("Email to Alice", topic="follow up")
    reg.add_ref(rec.id, "thread", thread_id)
    reg.set_state(rec.id, "waiting_on_others", "waiting for reply")
    return reg


def test_first_notification_seeds_history_without_backfill(tmp_path):
    service, gateway, dispatched = make_service(tmp_path)
    result = asyncio.run(service.handle_pubsub(envelope(12345678901234567890)))
    assert result["status"] == "seeded"
    assert service.store.last_history_id == 12345678901234567890
    assert gateway.history_calls == [] and dispatched == []


def test_first_notification_for_wrong_account_is_rejected_when_profile_is_known(tmp_path):
    service, gateway, dispatched = make_service(tmp_path)
    result = asyncio.run(service.handle_pubsub(envelope(10, email="other@example.com")))
    assert result["status"] == "invalid" and result["reason"] == "wrong_account"
    assert service.store.last_history_id is None
    assert gateway.history_calls == [] and dispatched == []


def test_owned_reply_is_dispatched_and_marked_seen(tmp_path):
    reg = owned_registry(tmp_path)
    gateway = FakeGateway()
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"}]
    gateway.messages["m1"] = api_message()
    seen = set()
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg, seen=seen)
    service.store.set_history(10, ME)
    result = asyncio.run(service.handle_pubsub(envelope(20)))
    assert result["status"] == "processed"
    assert len(dispatched) == 1
    summary, email = dispatched[0]
    assert "Alice" in summary and "Re: Lunch" in summary
    assert email.thread_id == "thr_1" and email.sender == "Alice <alice@example.com>"
    assert service.store.last_history_id == 20
    assert "m1" in service.store.processed_ids and "m1" in seen
    assert gateway.history_calls == [("10", None)]


def test_duplicate_and_out_of_order_notifications_do_not_refetch(tmp_path):
    service, gateway, dispatched = make_service(tmp_path)
    service.store.set_history(30, ME)
    assert asyncio.run(service.handle_pubsub(envelope(30)))["status"] == "duplicate"
    assert asyncio.run(service.handle_pubsub(envelope(29)))["status"] == "stale"
    assert gateway.history_calls == [] and dispatched == []
    assert service.store.last_history_id == 30


def test_missed_notifications_are_caught_up_across_pages_in_order(tmp_path):
    reg = owned_registry(tmp_path, "thr_one")
    gateway = FakeGateway()
    gateway.pages = [
        {"history": [added("m2", "thr_one", history_id=18)], "historyId": "25", "nextPageToken": "next"},
        {"history": [added("m1", "thr_one", history_id=12)], "historyId": "30"},
    ]
    gateway.messages["m1"] = api_message("m1", "thr_one", subject="Re: first")
    gateway.messages["m2"] = api_message("m2", "thr_one", subject="Re: second")
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    result = asyncio.run(service.handle_pubsub(envelope(30)))
    assert result["status"] == "processed" and result["messages"] == 2
    assert gateway.history_calls == [("10", None), ("10", "next")]
    assert [email.id for _, email in dispatched] == ["m1", "m2"]


def test_expired_start_history_resyncs_without_replaying_backlog(tmp_path):
    service, gateway, dispatched = make_service(tmp_path)
    gateway.history_error = GmailApiError("history expired", status_code=404)
    service.store.set_history(10, ME)
    result = asyncio.run(service.handle_pubsub(envelope(99)))
    assert result["status"] == "resynced"
    assert service.store.last_history_id == 99
    assert dispatched == []


def test_unowned_and_own_messages_are_processed_once_but_not_dispatched(tmp_path):
    gateway = FakeGateway()
    gateway.pages = [{"history": [
        added("m-unowned", "thr_unknown", history_id=11),
        added("m-sent", "thr_own", labels=["SENT"], history_id=12),
    ], "historyId": "20"}]
    gateway.messages["m-unowned"] = api_message("m-unowned", "thr_unknown")
    gateway.messages["m-sent"] = api_message("m-sent", "thr_own", labels=["SENT"])
    service, _, dispatched = make_service(tmp_path, gateway)
    service.store.set_history(10, ME)
    result = asyncio.run(service.handle_pubsub(envelope(20)))
    assert result["messages"] == 2 and result["dispatched"] == 0
    assert dispatched == []
    assert gateway.message_calls == []
    assert {"m-unowned", "m-sent"} <= service.store.processed_ids


def test_overlapping_history_does_not_double_dispatch(tmp_path):
    reg = owned_registry(tmp_path)
    gateway = FakeGateway()
    gateway.messages["m1"] = api_message()
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"}]
    asyncio.run(service.handle_pubsub(envelope(20)))
    service.store.set_history(10, ME)  # simulate a retry after a crash before advancing history
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"}]
    asyncio.run(service.handle_pubsub(envelope(20)))
    assert len(dispatched) == 1


def test_partial_message_failure_retries_only_the_unprocessed_message(tmp_path):
    reg = owned_registry(tmp_path)
    gateway = FakeGateway()
    gateway.messages["m1"] = api_message("m1", "thr_1", subject="Re: first")
    gateway.messages["m2"] = api_message("m2", "thr_1", subject="Re: second")
    gateway.message_errors["m2"] = GmailApiError("temporary Gmail failure", status_code=500)
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=18), added("m2", "thr_1", history_id=19)], "historyId": "20"}]
    with pytest.raises(GmailApiError):
        asyncio.run(service.handle_pubsub(envelope(20)))
    assert [email.id for _, email in dispatched] == ["m1"]
    assert service.store.last_history_id == 10

    gateway.message_errors.clear()
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=18), added("m2", "thr_1", history_id=19)], "historyId": "20"}]
    result = asyncio.run(service.handle_pubsub(envelope(20)))
    assert result["status"] == "processed"
    assert [email.id for _, email in dispatched] == ["m1", "m2"]
    assert gateway.message_calls.count("m1") == 1


def test_concurrent_duplicate_notifications_dispatch_once(tmp_path):
    reg = owned_registry(tmp_path)
    gateway = FakeGateway()
    gateway.pages = [
        {"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"},
        {"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"},
    ]
    gateway.messages["m1"] = api_message()
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    async def run():
        return await asyncio.gather(service.handle_pubsub(envelope(20)), service.handle_pubsub(envelope(20)))
    results = asyncio.run(run())
    assert sorted(r["status"] for r in results) == ["duplicate", "processed"]
    assert len(dispatched) == 1


def test_malformed_wrong_account_negative_and_empty_history_do_not_mutate(tmp_path):
    service, gateway, dispatched = make_service(tmp_path)
    service.store.set_history(10, ME)
    before = service.store.last_history_id
    for payload in [
        {"message": {"data": "not-base64"}},
        envelope(""),
        envelope("-2"),
        envelope(0),
        envelope(11, email="other@example.com"),
        {"message": {}},
    ]:
        result = asyncio.run(service.handle_pubsub(payload))
        assert result["status"] == "invalid"
    assert service.store.last_history_id == before
    assert gateway.history_calls == [] and dispatched == []


def test_unicode_reply_and_zero_internal_date_are_safe(tmp_path):
    reg = owned_registry(tmp_path, "thr_unicode")
    gateway = FakeGateway()
    gateway.pages = [{"history": [added("mü", "thr_unicode", history_id=20)], "historyId": "20"}]
    gateway.messages["mü"] = api_message("mü", "thr_unicode", "Zoë <zoe@example.com>", "Re: café — ", "Naïve — ", internal_date="0")
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    result = asyncio.run(service.handle_pubsub(envelope(20)))
    assert result["status"] == "processed"
    assert "café" in dispatched[0][0] and dispatched[0][1].timestamp.tzinfo is not None


def test_watch_registration_and_renewal_use_expiration_safely(tmp_path):
    gateway = FakeGateway()
    service, _, _ = make_service(tmp_path, gateway)
    service.store.set_history(1, ME)
    first = service.register_or_renew_watch("projects/p/topics/openpoke-gmail", now_ms=1000, renew_before_ms=100)
    assert first["action"] == "registered"
    assert gateway.watch_calls == [("projects/p/topics/openpoke-gmail", ["INBOX"])]
    assert service.store.watch_expiration_ms == 1893456000000
    again = service.register_or_renew_watch("projects/p/topics/openpoke-gmail", now_ms=1000, renew_before_ms=100)
    assert again["action"] == "current" and len(gateway.watch_calls) == 1
    service.store.update_watch(100, 1100, "projects/p/topics/openpoke-gmail", ME)
    gateway.watch_response["expiration"] = "1100"
    renewed = service.register_or_renew_watch("projects/p/topics/openpoke-gmail", now_ms=1050, renew_before_ms=100)
    assert renewed["action"] == "renewed" and len(gateway.watch_calls) == 2
    assert service.store.watch_expiration_ms == 1100


def test_watch_registration_rejects_empty_topic_and_bad_expiration(tmp_path):
    service, gateway, _ = make_service(tmp_path)
    service.store.set_history(1, ME)
    with pytest.raises(ValueError):
        service.register_or_renew_watch("")
    gateway.watch_response = {"historyId": "100", "expiration": "not-a-number"}
    with pytest.raises(ValueError):
        service.register_or_renew_watch("projects/p/topics/openpoke-gmail")


def override_route_settings(app, **values):
    """FastAPI keys overrides by the exact dependency object; config reloads in other tests."""
    from types import SimpleNamespace

    def walk(router):
        for route in router.routes:
            for candidate in getattr(route, "_effective_candidates", []):
                if getattr(candidate, "path", "") in {"/api/v1/gmail/push", "/api/v1/gmail/push/watch"}:
                    for dep in candidate.dependant.dependencies:
                        app.dependency_overrides[dep.call] = lambda: SimpleNamespace(**values)
            nested = getattr(route, "original_router", None)
            if nested is not None:
                walk(nested)

    walk(app.router)


def test_default_push_state_path_honors_configured_data_dir(tmp_path, monkeypatch):
    from server.services.gmail.push import _default_push_path

    monkeypatch.delenv("OPENPOKE_MULTI_TENANT", raising=False)
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path / "custom-data"))
    assert _default_push_path() == tmp_path / "custom-data" / "gmail_push_state.json"


def test_watch_renewer_uses_injected_service_without_network(tmp_path):
    from server.services.gmail.push import GmailPushWatchRenewer

    service, gateway, _ = make_service(tmp_path)
    service.store.set_history(1, ME)
    renewer = GmailPushWatchRenewer("projects/p/topics/openpoke-gmail", service_factory=lambda: service, interval_seconds=9999)
    result = asyncio.run(renewer.check_once())
    assert result["action"] == "registered"
    assert gateway.watch_calls


def test_push_routes_ack_owned_notification_and_expose_watch_registration(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.services.gmail.push as push_mod

    reg = owned_registry(tmp_path)
    gateway = FakeGateway()
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"}]
    gateway.messages["m1"] = api_message()
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    monkeypatch.setattr(push_mod, "_service", service)
    from server.app import app
    override_route_settings(app, gmail_push_topic="projects/p/topics/openpoke-gmail", gmail_push_shared_secret="correct")
    client = TestClient(app)
    response = client.post("/api/v1/gmail/push?token=correct", json=envelope(20))
    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert len(dispatched) == 1
    invalid = client.post("/api/v1/gmail/push?token=correct", json={"message": {"data": "bad"}})
    assert invalid.status_code == 200 and invalid.json()["status"] == "invalid"
    watch = client.post("/api/v1/gmail/push/watch", headers={"Authorization": "Bearer offline-test-owner-token"})
    assert watch.status_code == 200 and watch.json()["action"] == "registered"
    monkeypatch.setattr(push_mod, "_service", None)
    app.dependency_overrides.clear()


def test_push_routes_fail_closed_in_multi_tenant_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.services.gmail.push as push_mod

    service, _, dispatched = make_service(tmp_path)
    monkeypatch.setattr(push_mod, "_service", service)
    monkeypatch.setenv("OPENPOKE_MULTI_TENANT", "1")
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path / "tenant-data"))
    from server.app import app
    override_route_settings(app, gmail_push_topic="projects/p/topics/openpoke-gmail", gmail_push_shared_secret="correct")
    try:
        client = TestClient(app)
        headers = {"X-Openpoke-Tenant": "alice"}
        response = client.post("/api/v1/gmail/push?token=correct", json=envelope(20), headers=headers)
        assert response.status_code == 503
        watch = client.post(
            "/api/v1/gmail/push/watch",
            headers={**headers, "Authorization": "Bearer offline-test-owner-token"},
        )
        assert watch.status_code == 503
        assert dispatched == []
    finally:
        monkeypatch.setattr(push_mod, "_service", None)
        app.dependency_overrides.clear()


def test_composio_gateway_uses_query_typed_proxy_parameters(monkeypatch):
    from server.services.gmail.push import ComposioGmailProxyGateway
    import server.services.gmail.client as client_mod

    calls = []
    class Tools:
        def proxy(self, **kwargs):
            calls.append(kwargs)
            class Response:
                def model_dump(self):
                    return {"status": 200, "data": {"history": []}}
            return Response()
    class FakeComposio:
        tools = Tools()
    monkeypatch.setattr(client_mod, "_get_composio_client", lambda: FakeComposio())

    gateway = ComposioGmailProxyGateway("user-1", connected_account_id="conn-1")
    assert gateway.list_history(10, page_token="next") == {"history": []}
    parameters = calls[0]["parameters"]
    assert parameters and all(p["type"] == "query" for p in parameters)
    assert {"name": "startHistoryId", "value": "10", "type": "query"} in parameters
    assert calls[0]["endpoint"] == "https://gmail.googleapis.com/gmail/v1/users/me/history"


def test_push_endpoint_enforces_optional_shared_secret(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.services.gmail.push as push_mod

    service, _, _ = make_service(tmp_path)
    monkeypatch.setattr(push_mod, "_service", service)
    from server.app import app
    override_route_settings(app, gmail_push_topic="projects/p/topics/openpoke-gmail", gmail_push_shared_secret="correct")
    try:
        client = TestClient(app)
        assert client.post("/api/v1/gmail/push", json=envelope(20)).status_code == 401
        assert client.post("/api/v1/gmail/push?token=wrong", json=envelope(20)).status_code == 401
        assert client.post("/api/v1/gmail/push?token=correct", json=envelope(20)).status_code == 200
    finally:
        monkeypatch.setattr(push_mod, "_service", None)
        app.dependency_overrides.clear()


def test_push_endpoint_fails_closed_without_shared_secret(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.services.gmail.push as push_mod

    service, _, _ = make_service(tmp_path)
    monkeypatch.setattr(push_mod, "_service", service)
    from server.app import app
    override_route_settings(app, gmail_push_topic="projects/p/topics/openpoke-gmail", gmail_push_shared_secret=None)
    try:
        client = TestClient(app)
        assert client.post("/api/v1/gmail/push", json=envelope(20)).status_code == 503
    finally:
        monkeypatch.setattr(push_mod, "_service", None)
        app.dependency_overrides.clear()


def test_watch_renewal_preserves_existing_history_cursor(tmp_path):
    gateway = FakeGateway()
    service, _, _ = make_service(tmp_path, gateway)
    service.store.set_history(10, ME)
    gateway.watch_response = {"historyId": "100", "expiration": "1893456000000"}
    result = service.register_or_renew_watch("projects/p/topics/openpoke-gmail", now_ms=1000)
    assert result["action"] == "registered"
    assert service.store.last_history_id == 10


def test_transient_registry_failure_is_retried_instead_of_consuming_reply(tmp_path):
    class FlakyRegistry:
        def __init__(self):
            self.calls = 0
        def owners(self, kind, value):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("registry temporarily unavailable")
            return [object()]

    registry = FlakyRegistry()
    gateway = FakeGateway()
    gateway.messages["m1"] = api_message()
    service, _, dispatched = make_service(tmp_path, gateway, registry=registry)
    service.store.set_history(10, ME)
    gateway.pages = [{"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"}]
    with pytest.raises(TimeoutError):
        asyncio.run(service.handle_pubsub(envelope(20)))
    assert service.store.last_history_id == 10
    assert "m1" not in service.store.processed_ids
    assert dispatched == []

    gateway.pages = [{"history": [added("m1", "thr_1", history_id=20)], "historyId": "20"}]
    result = asyncio.run(service.handle_pubsub(envelope(20)))
    assert result["status"] == "processed"
    assert len(dispatched) == 1


def test_push_endpoint_is_unavailable_when_push_is_not_configured(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.services.gmail.push as push_mod

    service, _, _ = make_service(tmp_path)
    monkeypatch.setattr(push_mod, "_service", service)
    from server.app import app
    override_route_settings(app, gmail_push_topic=None, gmail_push_shared_secret=None)
    try:
        client = TestClient(app)
        response = client.post("/api/v1/gmail/push", json=envelope(20))
        assert response.status_code == 503
    finally:
        monkeypatch.setattr(push_mod, "_service", None)
        app.dependency_overrides.clear()


def test_deleted_owned_message_is_skipped_without_blocking_later_replies(tmp_path):
    reg = owned_registry(tmp_path)
    gateway = FakeGateway()
    gateway.messages["m2"] = api_message("m2", "thr_1", subject="Re: later")
    gateway.message_errors["m1"] = GmailApiError("message deleted", status_code=404)
    gateway.pages = [{"history": [
        added("m1", "thr_1", history_id=18),
        added("m2", "thr_1", history_id=19),
    ], "historyId": "20"}]
    service, _, dispatched = make_service(tmp_path, gateway, registry=reg)
    service.store.set_history(10, ME)
    result = asyncio.run(service.handle_pubsub(envelope(20)))
    assert result["status"] == "processed"
    assert [email.id for _, email in dispatched] == ["m2"]
    assert {"m1", "m2"} <= service.store.processed_ids
    assert service.store.last_history_id == 20


def test_watch_renewal_registers_changed_topic_instead_of_reusing_old_watch(tmp_path):
    gateway = FakeGateway()
    service, _, _ = make_service(tmp_path, gateway)
    service.store.set_history(10, ME)
    service.store.update_watch(10, 1893456000000, "projects/p/topics/old-topic", ME)
    result = service.register_or_renew_watch("projects/p/topics/new-topic", now_ms=1000)
    assert result["action"] == "renewed"
    assert gateway.watch_calls[-1] == ("projects/p/topics/new-topic", ["INBOX"])
    assert service.store.last_history_id == 10


def test_composio_gateway_discovers_connected_account_from_items_response(monkeypatch):
    from server.services.gmail.push import ComposioGmailProxyGateway
    import server.services.gmail.client as client_mod

    class Account:
        id = "conn-real"
    class ConnectedAccounts:
        def list(self, **kwargs):
            class Response:
                items = [Account()]
            return Response()
    class Tools:
        def proxy(self, **kwargs):
            class Response:
                def model_dump(self):
                    return {"status": 200, "data": {"emailAddress": ME}}
            return Response()
    class FakeComposio:
        connected_accounts = ConnectedAccounts()
        tools = Tools()
    monkeypatch.setattr(client_mod, "_get_composio_client", lambda: FakeComposio())

    gateway = ComposioGmailProxyGateway("user-1")
    assert gateway._connected_account_id() == "conn-real"
