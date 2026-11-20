"""B0: OpenPoke today. Every agent name on every turn, upstream prompt and tools."""

from __future__ import annotations

from pathlib import Path

from evals.schema import PreparedTurn, Scenario, World
from evals.setups.common import (
    UPSTREAM_PROMPT, UPSTREAM_TOOLS, exact_name_resolver, names_block, simple_tool_result, upstream_message,
)


class B0Names:
    name = "b0_names"

    def __init__(self) -> None:
        self._blocks: dict[tuple[int, int], str] = {}

    def prepare(self, world: World, scenario: Scenario, workdir: Path) -> PreparedTurn:
        key = (world.seed, world.size)
        if key not in self._blocks:
            self._blocks[key] = names_block(world)
        content, _ = upstream_message(world, scenario, "active_agents", self._blocks[key])
        resolve = exact_name_resolver(world)

        def handle_tool(name: str, args: dict) -> dict:
            if name == "send_message_to_agent":
                agent_name = str(args.get("agent_name", ""))
                return {"status": "submitted", "agent_name": agent_name,
                        "new_agent_created": resolve(agent_name) is None}
            return simple_tool_result(name)

        return PreparedTurn(
            system_prompt=UPSTREAM_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=UPSTREAM_TOOLS,
            handle_tool=handle_tool,
            resolve_target=resolve,
            prompt_chars=len(UPSTREAM_PROMPT) + len(content),
        )
