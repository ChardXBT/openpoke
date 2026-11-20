"""Ours: the real OpenPoke reference router, run against a private copy of the world per scenario."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import server.agents.interaction_agent.tools as tools_mod
from evals.harness.loader import load_world
from evals.harness.transcript import render_current_turn, render_history
from evals.schema import PreparedTurn, Scenario, World
from evals.setups.common import parse_agent_id, simple_tool_result
from server.agents.interaction_agent.agent import build_system_prompt, prepare_message_with_history
from server.agents.interaction_agent.turn_context import TurnContext, build_turn_context, current_turn
from server.services.execution.registry import AgentRegistry, use_registry
from server.services.execution.resolver import EventMeta
from server.services.profile.store import ProfileStore, use_profile_store

ABLATIONS = frozenset({"resolver", "contract", "related"})


class _NullRoster:
    def load(self):
        pass

    def get_agents(self):
        return []

    def add_agent(self, name):
        pass


class _NullLogs:
    def record_request(self, *args, **kwargs):
        pass


def _install_eval_side_effects() -> None:
    """Agents are never launched and nothing is written to OpenPoke's real data dir."""
    tools_mod.set_dispatcher(lambda name, instructions: None)
    tools_mod.get_agent_roster = lambda: _NullRoster()  # type: ignore[assignment]
    tools_mod.get_execution_agent_logs = lambda: _NullLogs()  # type: ignore[assignment]


class Ours:
    def __init__(self, ablate: Optional[set[str]] = None) -> None:
        self.ablate = set(ablate or ())
        unknown = self.ablate - ABLATIONS
        if unknown:
            raise ValueError(f"unknown ablations: {sorted(unknown)}")
        self.name = "ours" + "".join(f"-no_{a}" for a in sorted(self.ablate))
        self._bases: dict[tuple[int, int], AgentRegistry] = {}
        # Routing scenarios carry no personal facts: an empty, private profile (never the real one).
        self._profile = ProfileStore(Path(":memory:"))
        _install_eval_side_effects()

    def _base(self, world: World) -> AgentRegistry:
        key = (world.seed, world.size)
        if key not in self._bases:
            self._bases[key] = load_world(world)
        return self._bases[key]

    def prepare(self, world: World, scenario: Scenario, workdir: Path) -> PreparedTurn:
        reg = self._base(world).clone_in_memory()
        latest, message_type = render_current_turn(scenario.message, scenario.event)
        history = render_history(scenario.history, world)
        event = None
        if scenario.event is not None:
            event = EventMeta("email", scenario.event.thread_id, scenario.event.sender, scenario.event.subject)

        with use_registry(reg), use_profile_store(self._profile):
            if "resolver" in self.ablate:
                ctx = TurnContext(event=event)
            else:
                ctx = build_turn_context(latest, history, message_type, event)
            ctx.enforce = "contract" not in self.ablate
            ctx.show_related = "related" not in self.ablate
            messages = prepare_message_with_history(latest, history, message_type, event=event, ctx=ctx)
            system = build_system_prompt()
            tools = tools_mod.get_tool_schemas()
        world_ids = {a.id for a in world.agents}

        def handle_tool(name: str, args: dict) -> dict:
            if name not in ("send_message_to_agent", "search_agents"):
                return simple_tool_result(name)
            with use_registry(reg), use_profile_store(self._profile):
                token = current_turn.set(ctx)
                try:
                    result = tools_mod.handle_tool_call(name, args)
                finally:
                    current_turn.reset(token)
            payload = dict(result.payload or {})
            if name == "send_message_to_agent" and payload.get("status") == "submitted":
                agent_id = None if payload.get("new_agent_created") else parse_agent_id(str(payload.get("agent", "")))
                # An agent created earlier in this turn is still new work, not reuse of a world agent.
                if agent_id is not None and (agent_id in ctx.created or agent_id not in world_ids):
                    agent_id = None
                payload.setdefault("resolved_id", agent_id)
            return payload

        def resolve_target(agent_name: str) -> Optional[int]:
            """World agent id, or None for an agent created during this turn (a NEW decision)."""
            rec = reg.find(agent_name)
            if rec is None or rec.id in ctx.created or rec.id not in world_ids:
                return None
            return rec.id

        content = messages[0]["content"]
        return PreparedTurn(
            system_prompt=system,
            messages=messages,
            tools=tools,
            handle_tool=handle_tool,
            resolve_target=resolve_target,
            prompt_chars=len(system) + len(content),
        )
