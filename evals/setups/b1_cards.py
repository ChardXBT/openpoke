"""B1: the obvious fix. Every agent, with a one-line description, on every turn."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from evals.schema import PreparedTurn, Scenario, World
from evals.setups.common import (
    UPSTREAM_PROMPT, UPSTREAM_TOOLS, id_or_name_resolver, simple_tool_result, upstream_message,
)
from server.services.execution.loops import age_label
from server.services.execution.working_set import STATE_LABELS

GUIDANCE = (
    "\n\nThe <agents> block lists every execution agent as `#id name · status: detail · last active`. "
    "Reuse an agent by its exact name or its #id (e.g. agent_name \"#412\") when it holds the relevant "
    "thread, draft or person+topic; otherwise create a new agent with a specific name."
)


class B1Cards:
    name = "b1_cards"

    def __init__(self) -> None:
        self._blocks: dict[tuple[int, int], str] = {}

    def _block(self, world: World) -> str:
        now = datetime.strptime(world.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        lines = []
        for a in sorted(world.agents, key=lambda x: (x.created_at, x.id)):
            label = STATE_LABELS.get(a.state, a.state)
            detail = a.state_note or a.topic
            status = f"{'archived, ' if a.archived else ''}{label}" + (f": {detail}" if detail else "")
            lines.append(f"#{a.id} {escape(a.name, quote=False)} · {escape(status, quote=False)} · "
                         f"{age_label(a.last_active_at, now)}")
        return "\n".join(lines) or "None"

    def prepare(self, world: World, scenario: Scenario, workdir: Path) -> PreparedTurn:
        key = (world.seed, world.size)
        if key not in self._blocks:
            self._blocks[key] = self._block(world)
        content, _ = upstream_message(world, scenario, "agents", self._blocks[key])
        resolve = id_or_name_resolver(world)
        system = UPSTREAM_PROMPT + GUIDANCE

        def handle_tool(name: str, args: dict) -> dict:
            if name == "send_message_to_agent":
                target = resolve(str(args.get("agent_name", "")))
                if target is None:
                    return {"status": "submitted", "new_agent_created": True}
                return {"status": "submitted", "agent": f"#{target}", "new_agent_created": False}
            return simple_tool_result(name)

        return PreparedTurn(
            system_prompt=system,
            messages=[{"role": "user", "content": content}],
            tools=UPSTREAM_TOOLS,
            handle_tool=handle_tool,
            resolve_target=resolve,
            prompt_chars=len(system) + len(content),
        )
