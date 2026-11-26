from server.services.gmail.demo_mailbox import DemoMailbox
from server.services.gmail.processing import parse_gmail_fetch_response


def test_seeded_inbox_parses_like_gmail(tmp_path):
    box = DemoMailbox(tmp_path / "box.json")
    result = box.execute("GMAIL_FETCH_EMAILS", {"query": "label:INBOX", "max_results": 50})
    emails, _ = parse_gmail_fetch_response(result, query="label:INBOX")
    assert len(emails) >= 4
    alice = [e for e in emails if "alice" in e.sender.casefold()]
    assert alice and alice[0].thread_id and alice[0].subject


def test_search_filters_by_words_and_sender(tmp_path):
    box = DemoMailbox(tmp_path / "box.json")
    hits = box.execute("GMAIL_FETCH_EMAILS", {"query": "from:bob invoice"})["data"]["messages"]
    assert hits and all("bob" in m["sender"].casefold() for m in hits)


def test_draft_send_reply_flow_and_outbox(tmp_path):
    box = DemoMailbox(tmp_path / "box.json")
    draft = box.execute("GMAIL_CREATE_EMAIL_DRAFT", {"recipient_email": "alice.chen@acme.com", "subject": "Q3 deck", "body": "Hi"})
    draft_id = draft["data"]["id"]
    assert draft["successful"] and draft_id.startswith("dr_")
    assert len(box.execute("GMAIL_LIST_DRAFTS", {})["data"]["drafts"]) == 1
    sent = box.execute("GMAIL_SEND_DRAFT", {"draft_id": draft_id})
    assert sent["successful"] and box.outbox()[-1]["to"] == "alice.chen@acme.com"
    assert box.execute("GMAIL_LIST_DRAFTS", {})["data"]["drafts"] == []
    thread = box.messages()[0]["threadId"]
    reply = box.execute("GMAIL_REPLY_TO_THREAD", {"thread_id": thread, "recipient_email": "x@y.com", "message_body": "ok"})
    assert reply["successful"] and box.outbox()[-1]["threadId"] == thread
    assert box.execute("GMAIL_SEND_DRAFT", {"draft_id": "dr_nope"})["successful"] is False


def test_inject_incoming_and_persistence(tmp_path):
    path = tmp_path / "box.json"
    box = DemoMailbox(path)
    msg = box.inject("Alice Chen <alice.chen@acme.com>", "Re: Lunch Thursday?", "Thursday 12:30 works!", thread_id=None)
    assert msg["labelIds"][:2] == ["INBOX", "UNREAD"]
    again = DemoMailbox(path)  # reloads from disk
    assert any(m["messageId"] == msg["messageId"] for m in again.messages())
    # Replying on the lunch thread keeps the seeded thread id.
    lunch = [m for m in again.messages() if "Lunch" in m["subject"]]
    assert len({m["threadId"] for m in lunch}) == 1


def test_people_and_profile(tmp_path):
    box = DemoMailbox(tmp_path / "box.json")
    people = box.execute("GMAIL_SEARCH_PEOPLE", {"query": "alice"})["data"]["people"]
    assert people[0]["emailAddresses"][0]["value"] == "alice.chen@acme.com"
    assert box.execute("GMAIL_GET_PROFILE", {})["data"]["emailAddress"].endswith("@demo.local")


def test_client_routes_to_demo_mailbox(tmp_path, monkeypatch):
    import server.services.gmail.client as client_mod
    import server.services.gmail.demo_mailbox as demo_mod
    from server.config import Settings

    monkeypatch.setattr(client_mod, "get_settings", lambda: Settings(fake_gmail=True))
    monkeypatch.setattr(demo_mod, "_mailbox", DemoMailbox(tmp_path / "box.json"))
    assert client_mod.get_active_gmail_user_id() == "demo-user"
    result = client_mod.execute_gmail_tool("GMAIL_FETCH_EMAILS", "demo-user", arguments={"query": "label:INBOX"})
    assert result["data"]["messages"]


def test_demo_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import server.routes.demo as demo_routes
    import server.services.gmail.demo_mailbox as demo_mod
    from server.config import Settings
    from server.services.execution.registry import AgentRegistry, set_agent_registry
    from server.services.profile.store import ProfileStore, _override

    monkeypatch.setattr(demo_mod, "_mailbox", DemoMailbox(tmp_path / "box.json"))
    set_agent_registry(AgentRegistry(tmp_path / "agents.db"))
    token = _override.set(ProfileStore(tmp_path / "p.db"))
    try:
        from server.app import app

        auth = {"Authorization": "Bearer offline-test-owner-token"}
        client = TestClient(app)
        monkeypatch.setattr(demo_routes, "get_settings", lambda: Settings(fake_gmail=False))
        assert client.get("/api/v1/demo/mailbox", headers=auth).status_code == 404
        monkeypatch.setattr(demo_routes, "get_settings", lambda: Settings(fake_gmail=True))
        assert client.get("/api/v1/demo/mailbox", headers=auth).json()["inbox"]
        assert client.get("/api/v1/agents/open", headers=auth).json() == {"open": [], "recent": [], "total": 0}
        assert client.get("/api/v1/me", headers=auth).json() == {"facts": []}
    finally:
        _override.reset(token)
        set_agent_registry(None)


def test_reply_to_last_threads_onto_what_you_sent(tmp_path):
    box = DemoMailbox(tmp_path / "box.json")
    draft = box.execute("GMAIL_CREATE_EMAIL_DRAFT", {"recipient_email": "alice.chen@acme.com", "subject": "Thursday lunch", "body": "Free?"})
    sent = box.execute("GMAIL_SEND_DRAFT", {"draft_id": draft["data"]["id"]})
    reply = box.inject("Alice Chen <alice.chen@acme.com>", "Re: Lunch Thursday?", "Yes!", reply_to_last=True)
    assert reply["threadId"] == sent["data"]["threadId"] and reply["subject"] == "Re: Thursday lunch"
    # Nobody emailed Bob: his email threads by subject as before.
    bob = box.inject("Bob Stone <bob@northwind.com>", "Re: Invoice #402", "Got it?", reply_to_last=True)
    assert bob["threadId"] == "thr_bob_402"
