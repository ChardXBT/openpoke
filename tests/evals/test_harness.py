import asyncio
import json

import pytest

from evals.harness import runner
from evals.harness.grade import Decision, extract_decision, grade, paired_delta, summarize, _bootstrap
from evals.harness.llm import Budget, BudgetExceeded, LLMClient, LLMResult, Pricing, Price
from evals.schema import Contact, EmailEvent, Gold, Scenario, Turn, World, WorldAgent
from evals.setups.b0_names import B0Names
from evals.setups.common import UPSTREAM_PROMPT, UPSTREAM_TOOLS
from evals.setups.ours import Ours

NOW = "2026-11-20T15:00:00Z"


def _agent(i, name, state, note="", topic="", refs=(), last="2026-11-20T14:00:00Z", archived=False):
    return WorldAgent(id=i, name=name, topic=topic, created_at="2026-11-01T10:00:00Z", last_active_at=last,
                      state=state, state_note=note, refs=list(refs), archived=archived, core=True)


def make_world(size: int = 6, seed: int = 1) -> World:
    agents = [
        _agent(1, "Email to Alice – Q3 deck", "waiting_on_user", 'draft to alice.chen@acme.com re "Q3 deck v2" awaiting your OK',
               "Draft the Q3 deck email to Alice", [("contact", "alice.chen@acme.com"), ("contact_name", "Alice Chen"), ("draft", "dr_1")]),
        _agent(2, "Bob invoice follow-up", "waiting_on_user", 'draft to bob@northwind.com re "Invoice #402" awaiting your OK',
               "Chase Bob about invoice #402", [("contact", "bob@northwind.com"), ("contact_name", "Bob Stone"), ("draft", "dr_2")]),
        _agent(3, "Alice – lunch Thursday", "waiting_on_others", 'sent to alice.chen@acme.com re "Lunch Thursday?"; waiting for reply',
               "Ask Alice about lunch", [("contact", "alice.chen@acme.com"), ("contact_name", "Alice Chen"), ("thread", "thr_lunch1")]),
        _agent(4, "Japan visa paperwork", "done", "", "Collect documents for the Japan visa", [("thread", "thr_visa9")],
               last="2026-09-01T10:00:00Z", archived=True),
        _agent(5, "Tokyo ramen search", "done", "", "Find ramen in Shinjuku", last="2026-11-10T10:00:00Z"),
        _agent(6, "Dentist reminder (Vancouver)", "scheduled", "reminder scheduled (2026-11-25T09:00)", "Remind me about the dentist"),
    ]
    contacts = [Contact("c01", "Alice Chen", "alice.chen@acme.com", "Acme"), Contact("c02", "Bob Stone", "bob@northwind.com", "Northwind")]
    return World(size=size, seed=seed, now=NOW, contacts=contacts, agents=agents[:size])


TWO_DRAFTS = [
    Turn("user", "draft the deck email to alice and the invoice nudge to bob", "2026-11-20T14:00:00Z"),
    Turn("agent", "Draft ready for alice.chen@acme.com", "2026-11-20T14:01:00Z", agent_id=1),
    Turn("agent", "Draft ready for bob@northwind.com", "2026-11-20T14:01:05Z", agent_id=2),
    Turn("poke", "both drafts are ready, want me to send them?", "2026-11-20T14:01:30Z"),
]


def make_scenarios(world: World) -> list[Scenario]:
    return [
        Scenario("dev-04-001", 4, "dev", TWO_DRAFTS, "yep send it", None, Gold("ASK"), True, "two drafts surfaced"),
        Scenario("dev-03-001", 3, "dev", TWO_DRAFTS[:2] + [Turn("poke", "To: alice.chen@acme.com\nSubject: Q3 deck v2", "2026-11-20T14:01:30Z")],
                 "send it", None, Gold("agent", 1), True, "one draft surfaced"),
        Scenario("dev-06-001", 6, "dev", [], "find me a dentist in toronto open saturdays", None, Gold("NEW"), False, "new task"),
        Scenario("dev-08-001", 8, "dev", [], None,
                 EmailEvent("thr_lunch1", "alice.chen@acme.com", "Re: Lunch Thursday?", "Alice says Thursday works."),
                 Gold("agent", 3), False, "reply on waiting thread"),
    ]


# ---------------------------------------------------------------- budget and client
def test_budget_blocks_over_cap(tmp_path):
    budget = Budget(0.01, tmp_path / "ledger.json")
    budget.charge(0.009)
    with pytest.raises(BudgetExceeded):
        budget.check(0.002)
    budget.check(0.0005)


def test_budget_persists(tmp_path):
    Budget(1.0, tmp_path / "l.json").charge(0.25)
    assert Budget(1.0, tmp_path / "l.json").spent() == pytest.approx(0.25)


class _Pricing(Pricing):
    def __init__(self):
        self._prices = {"m": Price(1e-6, 2e-6, 100_000)}


def test_llm_client_charges_reported_cost(tmp_path):
    seen = {}

    async def transport(url, headers, payload):
        seen.update(payload)
        return 200, {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0042}}

    budget = Budget(1.0, tmp_path / "l.json")
    client = LLMClient("m", budget, max_tokens=77, pricing=_Pricing(), transport=transport)
    res = asyncio.run(client.complete("sys", [{"role": "user", "content": "x"}], []))
    assert res.cost_usd == pytest.approx(0.0042) and budget.spent() == pytest.approx(0.0042)
    assert seen["max_tokens"] == 77 and seen["temperature"] == 0
    assert seen["messages"][0] == {"role": "system", "content": "sys"}


def test_llm_client_402_stops(tmp_path):
    async def transport(url, headers, payload):
        return 402, {"error": {"message": "Insufficient credits"}}

    client = LLMClient("m", Budget(1.0, tmp_path / "l.json"), pricing=_Pricing(), transport=transport)
    with pytest.raises(BudgetExceeded):
        asyncio.run(client.complete("s", [], []))


def test_openai_direct_payload(tmp_path, monkeypatch):
    seen = {}

    async def transport(url, headers, payload):
        seen["url"], seen["payload"] = url, payload
        return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = LLMClient("openai:gpt-6-luna", Budget(1.0, tmp_path / "l.json"), max_tokens=50, transport=transport)
    asyncio.run(client.complete("s", [], []))
    assert seen["url"].startswith("https://api.openai.com/")
    assert seen["payload"]["model"] == "gpt-6-luna" and seen["payload"]["max_completion_tokens"] == 50


# ---------------------------------------------------------------- grading
def _sc(gold, family=1):
    return Scenario("x", family, "dev", [], "m", None, gold, False)


def test_extract_decision_agent_new_ask_other():
    resolve = {"Known": 5}.get
    assert extract_decision([{"args": {"agent_name": "Known", "instructions": "look"}, "status": "submitted"}], [], resolve).kind == "agent"
    assert extract_decision([{"args": {"agent_name": "Fresh", "instructions": "x"}, "status": "submitted", "new_agent_created": True}], [], resolve).kind == "NEW"
    assert extract_decision([{"args": {"agent_name": "Known", "instructions": "send"}, "status": "needs_user_choice"}], ["which one?"], resolve).kind == "ASK"
    assert extract_decision([], ["ok noted."], resolve).kind == "OTHER"


def test_grade_outcomes():
    assert grade(Decision("agent", 1), _sc(Gold("agent", 1))) == "correct"
    assert grade(Decision("agent", 2), _sc(Gold("agent", 1, [2]))) == "correct"
    assert grade(Decision("NEW"), _sc(Gold("agent", 1))) == "duplicate"
    assert grade(Decision("agent", 9), _sc(Gold("agent", 1))) == "wrong_reuse"
    assert grade(Decision("agent", 9), _sc(Gold("NEW"))) == "wrong_reuse"
    assert grade(Decision("ASK", asked=True), _sc(Gold("ASK"))) == "correct"
    assert grade(Decision("agent", 1, 1, True, False), _sc(Gold("ASK"))) == "correct"  # asked, nothing irreversible
    assert grade(Decision("agent", 1, 1, False, True), _sc(Gold("ASK"))) == "missed_ask"
    assert grade(Decision("ASK", asked=True), _sc(Gold("agent", 1))) == "over_ask"
    assert grade(Decision("OTHER"), _sc(Gold("agent", 1))) == "other"
    assert grade(Decision("OTHER"), _sc(Gold("agent", 3), family=8)) == "correct"
    assert grade(Decision("ASK", asked=True), _sc(Gold("agent", 3), family=8)) == "correct"
    assert grade(Decision("NEW"), _sc(Gold("agent", 3), family=8)) == "duplicate"


def test_bootstrap_and_paired_delta_deterministic():
    assert _bootstrap([1.0, 0.0, 1.0, 1.0]) == _bootstrap([1.0, 0.0, 1.0, 1.0])
    a = [{"scenario_id": str(i), "size": 1, "rep": 0, "model": "m", "outcome": "correct"} for i in range(10)]
    b = [{"scenario_id": str(i), "size": 1, "rep": 0, "model": "m", "outcome": "correct" if i < 5 else "other"} for i in range(10)]
    delta, lo, hi, n = paired_delta(a, b)
    assert (delta, n) == (0.5, 10) and lo <= 0.5 <= hi


# ---------------------------------------------------------------- setups
def test_b0_matches_upstream(tmp_path):
    world = make_world()
    prepared = B0Names().prepare(world, make_scenarios(world)[0], tmp_path)
    assert prepared.system_prompt == UPSTREAM_PROMPT and prepared.tools == UPSTREAM_TOOLS
    content = prepared.messages[0]["content"]
    assert '<agent name="Email to Alice – Q3 deck" />' in content and "<active_agents>" in content
    assert prepared.handle_tool("send_message_to_agent", {"agent_name": "Nope", "instructions": "x"})["new_agent_created"] is True


def test_ours_uses_real_prompt_and_isolates_scenarios(tmp_path):
    world = make_world()
    scenarios = make_scenarios(world)
    ours = Ours()
    first = ours.prepare(world, scenarios[0], tmp_path)
    assert "Agents and Routing" in first.system_prompt
    assert any(t["function"]["name"] == "search_agents" for t in first.tools)
    assert "<routing_hint>\nAmbiguous" in first.messages[0]["content"]
    blocked = first.handle_tool("send_message_to_agent", {"agent_name": "#1", "instructions": "User confirmed. Send the draft."})
    assert blocked["status"] == "needs_user_choice"
    created = first.handle_tool("send_message_to_agent", {"agent_name": "Brand new thing", "instructions": "look something up"})
    assert created["status"] == "submitted" and created["new_agent_created"] is True
    assert first.resolve_target("Brand new thing") is None  # created this turn → NEW
    second = ours.prepare(world, scenarios[2], tmp_path)
    assert "Brand new thing" not in second.messages[0]["content"]  # nothing leaked between scenarios


# ---------------------------------------------------------------- loop
class FakeClient:
    model = "fake/model"

    def __init__(self, script, window=1_000_000, tmp=None):
        self.script = script
        self.window = window
        self.calls = 0
        self.budget = Budget(100.0, tmp / "ledger.json")

    def context_window(self):
        return self.window

    async def complete(self, system, messages, tools):
        self.calls += 1
        message = self.script(system, messages)
        return LLMResult(message, 100, 10, 0.001, 0.1)


def _tool(name, args, call_id="c1"):
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def ask_after_guard(system, messages):
    """Sends to #1 / 'Email to Alice – Q3 deck'; if the tool pushes back, asks the user."""
    last = messages[-1]
    if last["role"] == "user":
        name = "#1" if "Agents and Routing" in system else "Email to Alice – Q3 deck"
        return _tool("send_message_to_agent", {"agent_name": name, "instructions": "User confirmed. Send the draft."})
    if last["role"] == "tool" and json.loads(last["content"])["status"] == "error":
        return _tool("send_message_to_user", {"message": "which one, the deck or the invoice?"}, "c2")
    return {"content": ""}


def test_run_turn_ours_asks_b0_sends(tmp_path):
    world = make_world()
    scenario = make_scenarios(world)[0]
    ours_row = asyncio.run(runner.run_turn(Ours(), world, scenario, FakeClient(ask_after_guard, tmp=tmp_path), 0, tmp_path))
    b0_row = asyncio.run(runner.run_turn(B0Names(), world, scenario, FakeClient(ask_after_guard, tmp=tmp_path), 0, tmp_path))
    assert ours_row["outcome"] == "correct" and ours_row["guard_statuses"] == ["needs_user_choice"]
    assert b0_row["outcome"] == "missed_ask"
    assert ours_row["prompt_tokens"] == 300 and ours_row["iterations"] == 3


def test_does_not_fit_skipped_without_call(tmp_path):
    world = make_world()
    client = FakeClient(ask_after_guard, window=100, tmp=tmp_path)
    row = asyncio.run(runner.run_turn(B0Names(), world, make_scenarios(world)[0], client, 0, tmp_path))
    assert row["outcome"] == "does_not_fit" and client.calls == 0


def test_run_writes_rows_and_summary(tmp_path):
    client = FakeClient(ask_after_guard, tmp=tmp_path)
    out = tmp_path / "run"
    asyncio.run(runner.run([B0Names(), Ours()], "dev", [6], client, out, concurrency=4,
                           world_factory=make_world, scenario_factory=make_scenarios, log=lambda _m: None))
    rows = [json.loads(line) for line in (out / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 8
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert set(summary) == {"b0_names|fake/model|6", "ours|fake/model|6"}
    assert summary["ours|fake/model|6"]["clarify_recall"] == 1.0
