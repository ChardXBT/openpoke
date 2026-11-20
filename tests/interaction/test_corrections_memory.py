"""Learning from clarifications: the phrase the user used becomes an alias for the agent they chose."""

from server.agents.interaction_agent.turn_context import build_turn_context, current_turn


def _turn(env, message, agent_name, instructions, transcript=""):
    ctx = build_turn_context(message, transcript, "user")
    token = current_turn.set(ctx)
    try:
        return ctx, env.tools_mod.send_message_to_agent(agent_name, instructions)
    finally:
        current_turn.reset(token)


def _second_alice_draft(env) -> int:
    rec, _ = env.reg.get_or_create("Alice – Acme renewal", topic="Draft the Acme renewal redlines to Alice")
    env.reg.add_ref(rec.id, "contact", "alice.chen@acme.com")
    env.reg.set_state(rec.id, "waiting_on_user", 'draft to alice.chen@acme.com re "Acme renewal" awaiting your OK')
    return rec.id


def test_clarification_teaches_an_alias(routing):
    _second_alice_draft(routing)
    # 1. "the alice thing" fits two Alice items; an irreversible step is blocked and remembered.
    ctx, first = _turn(routing, "ok send the alice thing", "#1", "Send the draft now.")
    assert ctx.resolution.kind == "ambiguous"
    assert first.payload["status"] == "needs_user_choice"
    assert routing.reg.pending_choice() is not None

    # 2. The user picks the Q3 deck; the work goes to #1, so the phrase becomes #1's alias.
    _, second = _turn(routing, "the q3 deck one", "#1", "Send the Q3 deck draft now.")
    assert second.payload["status"] == "submitted"
    assert ("alias", "alice thing") in {(r.kind, r.value) for r in routing.reg.refs(1)}
    assert routing.reg.pending_choice() is None

    # 3. Next time the same phrase resolves in code, no question needed.
    ctx, _ = _turn(routing, "any update on the alice thing?", "#1", "Check status.")
    assert (ctx.resolution.kind, ctx.resolution.candidates, ctx.resolution.rule) == ("resolved", [1], "alias")


def test_no_alias_when_the_user_moves_on(routing):
    _second_alice_draft(routing)
    _turn(routing, "ok send the alice thing", "#1", "Send the draft now.")
    # The user goes somewhere else entirely: the pending choice is not attached to that agent.
    _turn(routing, "never mind, find me a ramen place", "Ramen search – Toronto", "Find ramen near King and Spadina")
    assert all(r.kind != "alias" for r in routing.reg.refs(routing.reg.find("Ramen search – Toronto").id))


def test_stale_pending_choice_is_ignored(routing):
    _second_alice_draft(routing)
    _turn(routing, "ok send the alice thing", "#1", "Send the draft now.")
    routing.reg.set_meta("pending_choice", '{"phrase": "alice thing", "candidates": [1, 2], "at": "2026-11-01T00:00:00Z"}')
    _turn(routing, "the q3 deck one", "#1", "Send the Q3 deck draft now.")
    assert all(r.kind != "alias" for r in routing.reg.refs(1))


def test_agent_message_turn_does_not_answer_the_question(routing):
    """Between "which one?" and the user's answer, an agent result or email routes work to one
    candidate. That is not the user's answer and must not teach the alias."""
    renewal = _second_alice_draft(routing)
    _turn(routing, "ok send the alice thing", "#1", "Send the draft now.")
    assert renewal in routing.reg.pending_choice()["candidates"]
    ctx = build_turn_context("[SUCCESS] Alice – Acme renewal: redlines attached to the draft", "", "agent")
    token = current_turn.set(ctx)
    try:
        routing.tools_mod.send_message_to_agent(f"#{renewal}", "Attach the latest redlines to the draft.")
    finally:
        current_turn.reset(token)
    assert all(r.kind != "alias" for r in routing.reg.refs(renewal))
    assert routing.reg.pending_choice() is not None  # still waiting for the user's answer
