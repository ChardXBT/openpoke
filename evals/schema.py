"""Shared contract for the agent-overload evaluation.

Frozen after Task 1 of docs/superpowers/plans/2026-11-20-reference-routing.md.
World/scenario generators (evals/world) and setups (evals/setups) build against
these types; change them only with the plan owner's agreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol

State = Literal["running", "waiting_on_user", "waiting_on_others", "scheduled", "done", "failed"]
RefKind = Literal["contact", "contact_name", "thread", "draft", "trigger", "subject", "alias"]


@dataclass(frozen=True)
class Contact:
    id: str  # "c01"
    name: str  # "Alice Chen"
    email: str  # "alice.chen@acme.com"
    company: str


@dataclass
class WorldAgent:
    id: int  # 1..N, stable; core agents use the lowest ids
    name: str  # as the IA would have named it
    topic: str  # the IA's first instruction, one line
    created_at: str  # UTC ISO 8601, "Z"
    last_active_at: str
    state: State
    state_note: str  # one line, never an email body
    refs: list[tuple[RefKind, str]] = field(default_factory=list)
    archived: bool = False
    has_active_trigger: bool = False
    core: bool = False  # True if any scenario depends on it


@dataclass
class Turn:
    role: Literal["user", "poke", "agent"]  # user_message / poke_reply / agent_message
    text: str  # agent turns: the response text only
    ts: str  # UTC ISO
    agent_id: Optional[int] = None  # set for role == "agent"
    status: Literal["SUCCESS", "FAILED"] = "SUCCESS"


@dataclass
class EmailEvent:
    thread_id: str
    sender: str  # email address
    subject: str
    summary: str  # what the importance classifier would write


@dataclass
class Gold:
    kind: Literal["agent", "NEW", "ASK"]
    agent_id: Optional[int] = None
    acceptable: list[int] = field(default_factory=list)  # other agent ids also correct


@dataclass
class Scenario:
    id: str  # "dev-03-007"
    family: int  # 1..10, spec section 7
    split: Literal["dev", "heldout"]
    history: list[Turn]
    message: Optional[str]  # user message, or None when event is set
    event: Optional[EmailEvent]
    gold: Gold
    irreversible: bool
    note: str = ""  # why the gold is what it is


@dataclass
class World:
    size: int
    seed: int
    now: str  # UTC ISO; all ages are relative to this
    contacts: list[Contact]
    agents: list[WorldAgent]  # len == size


@dataclass
class PreparedTurn:
    system_prompt: str
    messages: list[dict]
    tools: list[dict]
    handle_tool: Callable[[str, dict], dict]  # tool name, args -> JSON-able result
    resolve_target: Callable[[str], Optional[int]]  # agent_name or "#id" -> world agent id
    prompt_chars: int


class Setup(Protocol):
    name: str

    def prepare(self, world: World, scenario: Scenario, workdir: Path) -> PreparedTurn: ...
