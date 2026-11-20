from server.agents.interaction_agent.turn_context import TurnContext, current_turn
from server.services.execution.resolver import Resolution


def _call(env, resolution, *args, **kwargs):
    token = current_turn.set(TurnContext(resolution=resolution))
    try:
        return env.tools_mod.send_message_to_agent(*args, **kwargs)
    finally:
        current_turn.reset(token)


AMBIGUOUS = Resolution("ambiguous", [1, 2], "confirms a draft", "reference")
UNRESOLVED = Resolution("unresolved")


def test_ambiguous_irreversible_blocked(routing):
    result = _call(routing, AMBIGUOUS, "#1", "The user confirmed. Send the draft now.")
    assert result.payload["status"] == "needs_user_choice"
    assert len(result.payload["options"]) == 2
    assert routing.dispatched == []


def test_ambiguous_reversible_dispatched(routing):
    result = _call(routing, AMBIGUOUS, "#1", "Show me the current text of the draft again.")
    assert result.payload["status"] == "submitted"
    assert routing.dispatched == [("Email to Alice – Q3 deck", "Show me the current text of the draft again.")]


def test_resolved_single_not_blocked(routing):
    resolved = Resolution("resolved", [1], "confirms the draft", "reference")
    result = _call(routing, resolved, "#1", "The user confirmed. Send the draft.")
    assert result.payload["status"] == "submitted"


def test_confirm_target_when_resolution_points_elsewhere(routing):
    resolved = Resolution("resolved", [1], "confirms the draft", "reference")
    result = _call(routing, resolved, "#2", "Send the draft.")
    assert result.payload["status"] == "confirm_target"
    assert result.payload["suggested"][0].startswith("#1 ")
    assert routing.dispatched == []


def test_irreversible_to_non_waiting_target_confirms(routing):
    result = _call(routing, UNRESOLVED, "#3", "Send the draft now.")
    assert result.payload["status"] == "confirm_target"
    assert {card.split()[0] for card in result.payload["suggested"]} == {"#1", "#2"}


def test_thread_owned_spawn_redirects(routing):
    owned = Resolution("resolved", [3], "the incoming email is on its thread thr_lunch1", "event")
    result = _call(routing, owned, "Alice lunch reply", "Alice replied about lunch; summarize it.")
    assert result.payload["status"] == "owned_by"
    assert result.payload["agent"].startswith("#3 ")
    assert routing.reg.find("Alice lunch reply") is None


def test_possible_duplicate_then_create_new(routing):
    first = _call(routing, UNRESOLVED, "Email to Alice Q3 deck v3", "Update the deck email for alice.chen@acme.com")
    assert first.payload["status"] == "possible_duplicate"
    assert first.payload["candidates"][0].startswith("#1 ")
    second = _call(routing, UNRESOLVED, "Email to Alice Q3 deck v3", "Update the deck email", create_new=True)
    assert second.payload["status"] == "submitted"
    assert second.payload["new_agent_created"] is True


def test_same_turn_sibling_not_flagged(routing):
    ctx = TurnContext(resolution=UNRESOLVED)
    token = current_turn.set(ctx)
    try:
        a = routing.tools_mod.send_message_to_agent("Flight search – Tokyo", "Find flights to Tokyo in March")
        b = routing.tools_mod.send_message_to_agent("Flight search – Tokyo hotels", "Find hotels in Tokyo in March")
    finally:
        current_turn.reset(token)
    assert a.payload["status"] == "submitted" and b.payload["status"] == "submitted"
    assert len(routing.dispatched) == 2


def test_hash_id_and_normalized_name_reuse(routing):
    by_id = _call(routing, UNRESOLVED, "#2", "Check whether Bob opened the invoice email")
    by_name = _call(routing, UNRESOLVED, "bob INVOICE follow-up!!", "Look up Bob's last reply")
    assert by_id.payload == {"status": "submitted", "agent": "#2 Bob invoice follow-up", "new_agent_created": False}
    assert by_name.payload["agent"] == "#2 Bob invoice follow-up"
    assert routing.reg.count() == 4


def test_dispatch_marks_agent_working(routing):
    _call(routing, UNRESOLVED, "Tokyo ramen search", "Find ramen in Shinjuku")
    assert routing.reg.find("Tokyo ramen search").state == "running"


def test_search_agents_tool(routing):
    result = routing.tools_mod.search_agents("japan visa", include_archived=True)
    assert result.payload["agents"][0].startswith("#4 Japan visa paperwork")
    assert routing.tools_mod.search_agents("zzzz").payload["agents"] == ["No matching agents."]


def test_legacy_mode_unchanged(routing):
    routing.state["mode"] = "legacy"
    assert routing.tools_mod.get_tool_schemas() is routing.tools_mod.TOOL_SCHEMAS
    result = routing.tools_mod.handle_tool_call(
        "send_message_to_agent", {"agent_name": "Brand new", "instructions": "x", "create_new": True}
    )
    assert result.payload == {"status": "submitted", "agent_name": "Brand new", "new_agent_created": True}
    assert routing.roster.agents == ["Brand new"]
    assert routing.reg.find("Brand new") is None
    assert routing.tools_mod.handle_tool_call("search_agents", {"query": "x"}).payload["error"].startswith("Unknown tool")


def test_contract_narrows_by_action_state(routing):
    # Ambiguous between a draft (#1) and an item waiting on a reply (#3). "Cancel the draft"
    # can only apply to the draft, so choosing #1 is not a guess.
    mixed = Resolution("ambiguous", [1, 3], "open item for alice", "entity")
    allowed = _call(routing, mixed, "#1", "Cancel the draft, the user no longer wants it.")
    assert allowed.payload["status"] == "submitted"
    # Two drafts: a send still fits both, so it is still blocked.
    blocked = _call(routing, AMBIGUOUS, "#1", "Send the draft.")
    assert blocked.payload["status"] == "needs_user_choice"


def test_forwarding_from_a_finished_agent_is_not_challenged(routing):
    # #4 is archived/done; drafts are waiting elsewhere. Forwarding its findings is fine.
    result = _call(routing, UNRESOLVED, "#4", "Forward the visa checklist to linda@example.com.")
    assert result.payload["status"] == "submitted"


def test_multi_task_turn_new_agent_not_owned_by(routing):
    # "yes send it, and also find flights to tokyo": the reference settles the draft, but a
    # new, unrelated agent in the same turn is not a duplicate of it.
    resolved = Resolution("resolved", [1], "confirms the draft Poke just showed", "reference")
    result = _call(routing, resolved, "Flight search – Tokyo", "Find flights to Tokyo in March")
    assert result.payload["status"] == "submitted" and result.payload["new_agent_created"] is True


def test_related_new_agent_still_owned_by(routing):
    resolved = Resolution("resolved", [3], "the incoming email is on its thread thr_lunch1", "event")
    result = _call(routing, resolved, "Lunch reply", "Read Alice's reply about lunch")
    assert result.payload["status"] == "owned_by"


def test_forward_from_other_kind_of_agent_not_challenged(routing):
    # The message resolved to a draft (#1); forwarding from a finished agent (#4) is other work.
    resolved = Resolution("resolved", [1], "confirms the draft", "reference")
    result = _call(routing, resolved, "#4", "Forward the visa checklist to linda@example.com.")
    assert result.payload["status"] == "submitted"


def test_insisting_after_confirm_target_goes_through(routing):
    from server.agents.interaction_agent.turn_context import TurnContext, current_turn

    ctx = TurnContext(resolution=Resolution("resolved", [1], "confirms the draft", "reference"))
    token = current_turn.set(ctx)
    try:
        first = routing.tools_mod.send_message_to_agent("#2", "Send the draft.")
        second = routing.tools_mod.send_message_to_agent("#2", "Send the draft.")
    finally:
        current_turn.reset(token)
    assert first.payload["status"] == "confirm_target"
    assert second.payload["status"] == "submitted"  # the model saw the suggestion and insisted


def test_needs_user_choice_never_relents(routing):
    from server.agents.interaction_agent.turn_context import TurnContext, current_turn

    ctx = TurnContext(resolution=AMBIGUOUS)
    token = current_turn.set(ctx)
    try:
        results = [routing.tools_mod.send_message_to_agent("#1", "Send the draft.") for _ in range(3)]
    finally:
        current_turn.reset(token)
    assert [r.payload["status"] for r in results] == ["needs_user_choice"] * 3
    assert "Stop retrying" in results[-1].payload["message"]


# --- independent review ------------------------------------------------------------------
def _call_msg(env, resolution, message, *args, **kwargs):
    from server.agents.interaction_agent.turn_context import TurnContext, current_turn

    token = current_turn.set(TurnContext(resolution=resolution, message=message))
    try:
        return env.tools_mod.send_message_to_agent(*args, **kwargs)
    finally:
        current_turn.reset(token)


def test_ambiguous_send_blocked_even_when_instructions_avoid_send_words(routing):
    """The user said "send it" (ambiguous); the model words the instruction as "proceed"."""
    for wording in ("The user approved. Proceed with the draft.", "User confirmed - go ahead with it."):
        result = _call_msg(routing, AMBIGUOUS, "yes send it", "#1", wording)
        assert result.payload["status"] == "needs_user_choice", wording
    assert routing.dispatched == []


def test_multi_task_message_still_starts_new_work(routing):
    """The user's irreversible words do not block unrelated new work in the same turn."""
    result = _call_msg(routing, AMBIGUOUS, "send it, and also find flights to Tokyo",
                       "Flight search – Tokyo", "Find flights to Tokyo next week.")
    assert result.payload["status"] == "submitted"


def test_create_new_string_false_is_not_true(routing):
    """Models sometimes emit booleans as strings; "false" must not skip the duplicate guard."""
    result = routing.tools_mod.handle_tool_call("send_message_to_agent", {
        "agent_name": "Email to Alice Q3 deck v3",
        "instructions": "Update the deck email for alice.chen@acme.com",
        "create_new": "false",
    })
    assert result.payload["status"] == "possible_duplicate"
