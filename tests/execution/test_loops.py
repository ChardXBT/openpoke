from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution import loops
from server.services.execution.registry import AgentRegistry


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def reg(tmp_path, clock) -> AgentRegistry:
    return AgentRegistry(tmp_path / "agents.db", clock=clock)


def _no_triggers(_name: str) -> bool:
    return False


def test_begin_run_creates_and_marks_running(reg):
    agent_id = loops.begin_run(reg, "Email to Alice", "Draft the Q3 deck email to Alice")
    rec = reg.get(agent_id)
    assert rec.state == "running"
    assert rec.request_count == 1
    assert rec.topic == "Draft the Q3 deck email to Alice"


def test_draft_sets_waiting_on_user(reg):
    agent_id = loops.begin_run(reg, "Email to Alice", "draft it")
    loops.observe_tool_call(
        reg, agent_id, "gmail_create_draft",
        {"recipient_email": "Alice@Acme.com", "subject": "Q3 deck v2", "body": "Hi Alice, secret body text"},
        {"successful": True, "data": {"id": "dr_19c0", "message": {"threadId": "thr_8f2a"}}},
        True,
    )
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    rec = reg.get(agent_id)
    assert rec.state == "waiting_on_user"
    assert "alice@acme.com" in rec.state_note and "Q3 deck v2" in rec.state_note
    assert "secret body text" not in rec.state_note
    refs = {(r.kind, r.value) for r in reg.refs(agent_id)}
    assert {("contact", "alice@acme.com"), ("subject", "q3 deck v2"), ("draft", "dr_19c0"), ("thread", "thr_8f2a")} <= refs


def test_send_draft_uses_earlier_refs_and_waits_on_others(reg):
    agent_id = loops.begin_run(reg, "Email to Alice", "draft it")
    loops.observe_tool_call(reg, agent_id, "gmail_create_draft",
                            {"recipient_email": "alice@acme.com", "subject": "Q3 deck v2", "body": "x"},
                            {"data": {"id": "dr_19c0"}}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    agent_id = loops.begin_run(reg, "Email to Alice", "send it")
    loops.observe_tool_call(reg, agent_id, "gmail_execute_draft", {"draft_id": "dr_19c0"}, {"successful": True}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    rec = reg.get(agent_id)
    assert rec.state == "waiting_on_others"
    assert "alice@acme.com" in rec.state_note and "waiting for reply" in rec.state_note


def test_reply_sets_waiting_on_others_with_thread(reg):
    agent_id = loops.begin_run(reg, "Invoice 402 follow-up", "reply to bob")
    loops.observe_tool_call(reg, agent_id, "gmail_reply_to_thread",
                            {"thread_id": "thr_77", "recipient_email": "bob@x.com", "message_body": "body"},
                            {"successful": True}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    rec = reg.get(agent_id)
    assert rec.state == "waiting_on_others"
    assert ("thread", "thr_77") in {(r.kind, r.value) for r in reg.refs(agent_id)}


def test_error_payload_counts_as_failure(reg):
    agent_id = loops.begin_run(reg, "Email to Alice", "send it")
    loops.observe_tool_call(reg, agent_id, "gmail_execute_draft", {"draft_id": "dr_1"},
                            {"error": "Gmail not connected."}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    assert reg.get(agent_id).state == "done"


def test_failed_tool_call_sets_nothing(reg):
    agent_id = loops.begin_run(reg, "Email to Alice", "draft")
    loops.observe_tool_call(reg, agent_id, "gmail_create_draft", {"recipient_email": "a@b.com", "subject": "s"},
                            {"error": "boom"}, False)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    assert reg.get(agent_id).state == "done"
    assert reg.refs(agent_id) == []


def test_trigger_sets_scheduled_and_flag(reg):
    agent_id = loops.begin_run(reg, "Gym reminder", "remind me mon/wed/fri 7am")
    loops.observe_tool_call(reg, agent_id, "createTrigger",
                            {"payload": "gym", "recurrence_rule": "FREQ=WEEKLY;BYDAY=MO,WE,FR"},
                            {"trigger_id": 12, "status": "active"}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=lambda name: True)
    rec = reg.get(agent_id)
    assert rec.state == "scheduled"
    assert rec.has_active_trigger is True
    assert ("trigger", "12") in {(r.kind, r.value) for r in reg.refs(agent_id)}


def test_paused_trigger_clears_flag(reg):
    agent_id = loops.begin_run(reg, "Gym reminder", "remind me")
    loops.observe_tool_call(reg, agent_id, "createTrigger", {"payload": "gym"}, {"trigger_id": 12}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=lambda name: True)
    agent_id = loops.begin_run(reg, "Gym reminder", "stop the reminder")
    loops.observe_tool_call(reg, agent_id, "updateTrigger", {"trigger_id": 12, "status": "paused"}, {"ok": True}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    rec = reg.get(agent_id)
    assert rec.has_active_trigger is False
    assert rec.state == "done"


def test_rescheduled_trigger_updates_note(reg):
    agent_id = loops.begin_run(reg, "Reminder – Call mom", "remind me at 6pm")
    loops.observe_tool_call(reg, agent_id, "createTrigger", {"payload": "call mom", "start_time": "2026-11-21T18:00:00"},
                            {"trigger_id": 1}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=lambda n: True)
    agent_id = loops.begin_run(reg, "Reminder – Call mom", "push it to 7pm")
    loops.observe_tool_call(reg, agent_id, "updateTrigger", {"trigger_id": 1, "start_time": "2026-11-21T19:00:00-05:00"},
                            {"ok": True}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=lambda n: True)
    rec = reg.get(agent_id)
    assert rec.state == "scheduled"
    assert "19:00" in rec.state_note and "18:00" not in rec.state_note


def test_trigger_fire_keeps_scheduled_agent_scheduled(reg):
    agent_id = loops.begin_run(reg, "Gym reminder", "remind me")
    loops.finish_run(reg, agent_id, True, has_active_trigger=lambda name: True)
    assert reg.get(agent_id).state == "scheduled"


def test_plain_and_failed_runs(reg):
    ok = loops.begin_run(reg, "Tokyo ramen search", "find ramen")
    loops.finish_run(reg, ok, True, has_active_trigger=_no_triggers)
    assert reg.get(ok).state == "done"
    bad = loops.begin_run(reg, "Flight search", "find flights")
    loops.finish_run(reg, bad, False, has_active_trigger=_no_triggers)
    assert reg.get(bad).state == "failed"


def test_sweep_lapses_and_archives(reg, clock):
    waiting = loops.begin_run(reg, "Invoice 402 follow-up", "reply")
    loops.observe_tool_call(reg, waiting, "gmail_reply_to_thread",
                            {"thread_id": "t", "recipient_email": "bob@x.com", "message_body": "b"}, {}, True)
    loops.finish_run(reg, waiting, True, has_active_trigger=_no_triggers)
    done = loops.begin_run(reg, "Tokyo ramen search", "find ramen")
    loops.finish_run(reg, done, True, has_active_trigger=_no_triggers)
    keeper = loops.begin_run(reg, "Gym reminder", "remind")
    loops.finish_run(reg, keeper, True, has_active_trigger=lambda n: True)
    reg.set_state(keeper, "done")  # state drifted, but the trigger flag still protects it

    clock.now += timedelta(days=8)
    assert loops.sweep(reg, clock.now) >= 1
    assert reg.get(waiting).state == "done"
    assert reg.get(done).archived is False

    clock.now += timedelta(days=7)  # 15 days idle
    loops.sweep(reg, clock.now)
    assert reg.get(done).archived is True
    # Lapsing is not activity: idle since day 0, so archived on day 15 like any done agent.
    assert reg.get(waiting).archived is True
    assert reg.get(keeper).archived is False  # active trigger: never archived


# --- independent review ------------------------------------------------------------------
def test_follow_up_without_send_keeps_the_draft_waiting(reg):
    """"What does the draft say?" runs the agent again but sends nothing: the draft is still
    awaiting the user's OK and must stay an open item (else "send it" can no longer find it)."""
    agent_id = loops.begin_run(reg, "Email to Alice", "draft it")
    loops.observe_tool_call(reg, agent_id, "gmail_create_draft",
                            {"recipient_email": "alice@acme.com", "subject": "Q3 deck v2"},
                            {"data": {"id": "dr_1"}}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    note = reg.get(agent_id).state_note
    loops.mark_running(reg, agent_id)  # the IA dispatches a follow-up
    agent_id = loops.begin_run(reg, "Email to Alice", "what does the draft say?")
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    rec = reg.get(agent_id)
    assert (rec.state, rec.state_note) == ("waiting_on_user", note)


def test_sending_the_draft_still_moves_on(reg):
    agent_id = loops.begin_run(reg, "Email to Alice", "draft it")
    loops.observe_tool_call(reg, agent_id, "gmail_create_draft", {"recipient_email": "alice@acme.com"},
                            {"data": {"id": "dr_1"}}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    agent_id = loops.begin_run(reg, "Email to Alice", "send it")
    loops.observe_tool_call(reg, agent_id, "gmail_execute_draft", {"draft_id": "dr_1"}, {"successful": True}, True)
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    assert reg.get(agent_id).state == "waiting_on_others"
    # and a later plain run is not "restored" to the old draft state
    agent_id = loops.begin_run(reg, "Email to Alice", "anything else?")
    loops.finish_run(reg, agent_id, True, has_active_trigger=_no_triggers)
    assert reg.get(agent_id).state == "done"
