from datetime import datetime, timedelta, timezone

import pytest

from server.config import Settings
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.registry import AgentRecord, AgentRegistry, Ref, normalize_name, set_agent_registry

NOW = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rec(i, name, state, note="", topic="", days=0.1, archived=False):
    return AgentRecord(
        id=i, name=name, name_key=normalize_name(name), created_at=_iso(days + 1), last_active_at=_iso(days),
        state=state, state_note=note, topic=topic, request_count=1, archived=archived, has_active_trigger=False,
    )


class FakeRoster:
    def __init__(self):
        self.agents: list[str] = []

    def load(self):
        pass

    def get_agents(self):
        return list(self.agents)

    def add_agent(self, name):
        if name not in self.agents:
            self.agents.append(name)


class FakeConversationLog:
    def __init__(self, transcript: str = ""):
        self.transcript = transcript
        self.entries: list[tuple[str, str]] = []

    def load_transcript(self):
        return self.transcript

    def record_user_message(self, content):
        self.entries.append(("user", content))

    def record_agent_message(self, content):
        self.entries.append(("agent", content))

    def record_reply(self, content):
        self.entries.append(("poke", content))

    def record_wait(self, reason):
        self.entries.append(("wait", reason))


@pytest.fixture()
def routing(tmp_path, monkeypatch):
    """Isolated reference-mode environment: temp registry, roster, logs, chat log; recorded dispatch."""
    import server.agents.interaction_agent.agent as agent_mod
    import server.agents.interaction_agent.runtime as runtime_mod
    import server.agents.interaction_agent.tools as tools_mod

    reg = AgentRegistry(tmp_path / "agents.db", clock=lambda: NOW)
    reg.bulk_insert(
        [
            rec(1, "Email to Alice – Q3 deck", "waiting_on_user", 'draft to alice.chen@acme.com re "Q3 deck v2" awaiting your OK', "Draft the Q3 deck email to Alice"),
            rec(2, "Bob invoice follow-up", "waiting_on_user", 'draft to bob@northwind.com re "Invoice #402" awaiting your OK', "Chase Bob about invoice #402"),
            rec(3, "Alice – lunch Thursday", "waiting_on_others", 'sent to alice.chen@acme.com re "Lunch Thursday?"; waiting for reply', "Ask Alice about lunch Thursday"),
            rec(4, "Japan visa paperwork", "done", "", "Collect documents for the Japan visa", days=40, archived=True),
        ],
        {
            1: [Ref("contact", "alice.chen@acme.com"), Ref("contact_name", "Alice Chen"), Ref("draft", "dr_19c0")],
            2: [Ref("contact", "bob@northwind.com"), Ref("contact_name", "Bob Stone"), Ref("draft", "dr_77aa")],
            3: [Ref("contact", "alice.chen@acme.com"), Ref("contact_name", "Alice Chen"), Ref("thread", "thr_lunch1")],
            4: [Ref("thread", "thr_visa9")],
        },
    )
    set_agent_registry(reg)

    state = {"mode": "reference"}

    def settings():
        return Settings(openrouter_api_key="test-key", router_mode=state["mode"], conversation_summary_threshold=0)

    for mod in (agent_mod, tools_mod, runtime_mod):
        monkeypatch.setattr(mod, "get_settings", settings)

    roster = FakeRoster()
    logs = ExecutionAgentLogStore(tmp_path / "logs")
    chat = FakeConversationLog()
    monkeypatch.setattr(agent_mod, "get_agent_roster", lambda: roster)
    monkeypatch.setattr(tools_mod, "get_agent_roster", lambda: roster)
    monkeypatch.setattr(tools_mod, "get_execution_agent_logs", lambda: logs)
    monkeypatch.setattr(tools_mod, "get_conversation_log", lambda: chat)
    monkeypatch.setattr(runtime_mod, "get_conversation_log", lambda: chat)

    dispatched: list[tuple[str, str]] = []
    tools_mod.set_dispatcher(lambda name, instructions: dispatched.append((name, instructions)))

    class Env:
        pass

    env = Env()
    env.reg, env.roster, env.chat, env.dispatched, env.state = reg, roster, chat, dispatched, state
    env.agent_mod, env.tools_mod, env.runtime_mod = agent_mod, tools_mod, runtime_mod
    yield env
    tools_mod.set_dispatcher(None)
    set_agent_registry(None)
