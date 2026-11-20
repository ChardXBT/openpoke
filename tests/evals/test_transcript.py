from evals.harness.transcript import render_current_turn, render_history
from evals.schema import EmailEvent, Turn, World, WorldAgent


def _world() -> World:
    agent = WorldAgent(
        id=7, name="Email to Alice – Q3 deck", topic="Draft the Q3 deck email to Alice",
        created_at="2026-11-18T10:00:00Z", last_active_at="2026-11-20T13:00:00Z",
        state="waiting_on_user", state_note="draft to alice@acme.com awaiting your OK",
    )
    return World(size=1, seed=1, now="2026-11-20T15:00:00Z", contacts=[], agents=[agent])


def test_render_history_format():
    turns = [
        Turn(role="user", text="draft the deck email to alice", ts="2026-11-20T12:58:00Z"),
        Turn(role="poke", text="on it", ts="2026-11-20T12:58:05Z"),
        Turn(role="agent", text="Draft ready: To alice@acme.com <Q3 & deck>", ts="2026-11-20T13:00:00Z", agent_id=7),
    ]
    out = render_history(turns, _world())
    lines = out.splitlines()
    assert lines[0] == '<user_message timestamp="2026-11-20 12:58:00">draft the deck email to alice</user_message>'
    assert lines[1] == '<poke_reply timestamp="2026-11-20 12:58:05">on it</poke_reply>'
    assert lines[2] == (
        '<agent_message timestamp="2026-11-20 13:00:00">'
        "[SUCCESS] Email to Alice – Q3 deck: Draft ready: To alice@acme.com &lt;Q3 &amp; deck&gt;"
        "</agent_message>"
    )


def test_failed_agent_turn_and_empty_history():
    turns = [Turn(role="agent", text="timeout", ts="2026-11-20T13:00:00Z", agent_id=7, status="FAILED")]
    assert "[FAILED] Email to Alice – Q3 deck: timeout" in render_history(turns, _world())
    assert render_history([], _world()) == ""


def test_render_current_turn_user_and_event():
    assert render_current_turn("send it", None) == ("send it", "user")
    event = EmailEvent(thread_id="t1", sender="bob@x.com", subject="Invoice #402", summary="Bob replied about invoice #402.")
    text, kind = render_current_turn(None, event)
    assert kind == "agent"
    assert text == "Important email watcher notification:\nBob replied about invoice #402."
