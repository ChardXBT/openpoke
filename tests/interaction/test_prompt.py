from pathlib import Path

from server.agents.interaction_agent.turn_context import TurnContext
from server.services.execution.resolver import EventMeta, Resolution

UPSTREAM = (Path(__file__).resolve().parents[2] / "evals" / "setups" / "upstream" / "system_prompt.md").read_text(
    encoding="utf-8"
).strip()


def test_reference_prompt_adds_routing_and_scopes_reuse(routing):
    prompt = routing.agent_mod.build_system_prompt()
    assert "Agents and Routing" in prompt
    assert "Don't worry if the agent name is unrelated" not in prompt
    assert "needs_user_choice" in prompt


def test_legacy_prompt_byte_identical(routing):
    routing.state["mode"] = "legacy"
    assert routing.agent_mod.build_system_prompt() == UPSTREAM


def test_reference_message_has_agents_hint_and_related(routing):
    ctx = TurnContext(resolution=Resolution("resolved", [1], "confirms the draft Poke just showed", "reference"))
    content = routing.agent_mod.prepare_message_with_history("any news on the japan visa?", "", "user", ctx=ctx)[0]["content"]
    assert "<agents>" in content and "#1 Email to Alice – Q3 deck · waiting on you" in content
    assert "<routing_hint>\nLikely about #1 Email to Alice – Q3 deck" in content
    assert "<related_agents>\n#4 Japan visa paperwork · archived, done" in content
    assert content.rstrip().endswith("<new_user_message>\nany news on the japan visa?\n</new_user_message>")
    assert "<active_agents>" not in content


def test_event_turn_resolves_and_uses_event_for_related(routing):
    event = EventMeta(kind="email", thread_id="thr_lunch1", sender="alice.chen@acme.com", subject="Re: Lunch Thursday?")
    content = routing.agent_mod.prepare_message_with_history(
        "Important email watcher notification:\nAlice said Thursday works.", "", "agent", event=event
    )[0]["content"]
    assert "Likely about #3 Alice – lunch Thursday" in content
    assert "<new_agent_message>" in content


def test_legacy_message_lists_names(routing):
    routing.state["mode"] = "legacy"
    routing.roster.add_agent('Odd "name" <x>')
    content = routing.agent_mod.prepare_message_with_history("hi", "", "user")[0]["content"]
    assert '<agent name="Odd &quot;name&quot; &lt;x&gt;" />' in content
    assert "<agents>" not in content and "<routing_hint>" not in content
