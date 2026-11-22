"""End-to-end through InteractionAgentRuntime with a scripted model (no network)."""

import asyncio
import json

TWO_DRAFTS = "\n".join([
    '<user_message timestamp="2026-11-20 14:00:00">draft the deck email to alice and the invoice nudge to bob</user_message>',
    '<agent_message timestamp="2026-11-20 14:01:00">[SUCCESS] Email to Alice – Q3 deck: Draft ready for alice.chen@acme.com\n'
    "[SUCCESS] Bob invoice follow-up: Draft ready for bob@northwind.com</agent_message>",
    '<poke_reply timestamp="2026-11-20 14:01:30">both drafts are ready, want me to send them?</poke_reply>',
])


def _tool_call(call_id, name, args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _response(content="", tool_calls=None):
    message = {"content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def test_ambiguous_send_becomes_a_question(routing, monkeypatch):
    routing.chat.transcript = TWO_DRAFTS
    seen_messages = []
    script = iter([
        _response(tool_calls=[_tool_call("c1", "send_message_to_agent",
                                         {"agent_name": "#1", "instructions": "User confirmed. Send the draft."})]),
        _response(tool_calls=[_tool_call("c2", "send_message_to_user",
                                         {"message": "which one, alice's deck or bob's invoice?"})]),
        _response(content=""),
    ])

    async def fake_completion(**kwargs):
        seen_messages.append(json.loads(json.dumps(kwargs["messages"])))
        return next(script)

    monkeypatch.setattr(routing.runtime_mod, "request_chat_completion", fake_completion)
    runtime = routing.runtime_mod.InteractionAgentRuntime()
    result = asyncio.run(runtime.execute("yep send it"))

    assert result.success
    assert routing.dispatched == []  # nothing was sent
    first_prompt = seen_messages[0][0]["content"]
    assert "<routing_hint>\nAmbiguous" in first_prompt
    tool_reply = json.loads(seen_messages[1][-1]["content"])
    assert tool_reply["result"]["status"] == "needs_user_choice" if tool_reply["status"] == "success" else \
        tool_reply["error"]["status"] == "needs_user_choice"
    assert result.response == "which one, alice's deck or bob's invoice?"


def test_clear_send_goes_through(routing, monkeypatch):
    routing.chat.transcript = "\n".join([
        '<agent_message timestamp="2026-11-20 14:01:00">[SUCCESS] Email to Alice – Q3 deck: Draft ready</agent_message>',
        '<poke_reply timestamp="2026-11-20 14:01:30">To: alice.chen@acme.com\nSubject: Q3 deck v2\n\nHi Alice</poke_reply>',
    ])
    script = iter([
        _response(tool_calls=[_tool_call("c1", "send_message_to_agent",
                                         {"agent_name": "#1", "instructions": "User confirmed. Send the draft."})]),
        _response(content="sending it now"),
    ])

    async def fake_completion(**kwargs):
        return next(script)

    monkeypatch.setattr(routing.runtime_mod, "request_chat_completion", fake_completion)
    result = asyncio.run(routing.runtime_mod.InteractionAgentRuntime().execute("yes send it"))
    assert result.success
    assert routing.dispatched == [("Email to Alice – Q3 deck", "User confirmed. Send the draft.")]


def test_repeated_ambiguous_send_ends_with_clarifying_question(routing, monkeypatch):
    routing.chat.transcript = TWO_DRAFTS
    calls = []

    async def fake_completion(**kwargs):
        calls.append(1)
        return _response(content="Sending it now.", tool_calls=[
            _tool_call("retry", "send_message_to_agent",
                       {"agent_name": "#1", "instructions": "User confirmed. Send the draft."}),
        ])

    monkeypatch.setattr(routing.runtime_mod, "request_chat_completion", fake_completion)
    runtime = routing.runtime_mod.InteractionAgentRuntime()
    result = asyncio.run(runtime.execute("yep send it"))

    assert result.success
    assert result.response == "Which item do you mean?"
    assert routing.chat.entries[-1] == ("poke", result.response)
    assert routing.dispatched == []
    assert len(calls) == runtime.MAX_TOOL_ITERATIONS


def test_iteration_limit_without_ambiguity_still_reports_failure(routing, monkeypatch):
    async def fake_completion(**kwargs):
        return _response(tool_calls=[_tool_call("retry", "unknown_tool", {})])

    monkeypatch.setattr(routing.runtime_mod, "request_chat_completion", fake_completion)
    result = asyncio.run(routing.runtime_mod.InteractionAgentRuntime().execute("hello"))
    assert not result.success
    assert result.error == "Reached tool iteration limit without final response"
