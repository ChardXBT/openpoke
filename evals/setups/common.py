"""Shared pieces for eval setups: the vendored upstream prompt/tools and message layout."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Optional

from evals.harness.transcript import render_current_turn, render_history
from evals.schema import Scenario, World

_UPSTREAM = Path(__file__).resolve().parent / "upstream"
UPSTREAM_PROMPT = (_UPSTREAM / "system_prompt.md").read_text(encoding="utf-8").strip()
UPSTREAM_TOOLS = json.loads((_UPSTREAM / "tools.json").read_text(encoding="utf-8"))
_HASH = re.compile(r"#(\d+)")


def upstream_message(world: World, scenario: Scenario, agents_tag: str, agents_text: str) -> tuple[str, str]:
    """(user message content, message_type) in upstream prepare_message_with_history layout."""
    latest, message_type = render_current_turn(scenario.message, scenario.event)
    history = render_history(scenario.history, world).strip() or "None"
    current_tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    content = (
        f"<conversation_history>\n{history}\n</conversation_history>\n\n"
        f"<{agents_tag}>\n{agents_text}\n</{agents_tag}>\n\n"
        f"<{current_tag}>\n{latest.strip()}\n</{current_tag}>"
    )
    return content, message_type


def names_block(world: World) -> str:
    """Upstream roster rendering: every agent as a bare name, in creation order."""
    ordered = sorted(world.agents, key=lambda a: (a.created_at, a.id))
    return "\n".join(f'<agent name="{escape(a.name or "agent", quote=True)}" />' for a in ordered) or "None"


def exact_name_resolver(world: World):
    by_name = {a.name: a.id for a in world.agents}

    def resolve(name: str) -> Optional[int]:
        return by_name.get(str(name))

    return resolve


def id_or_name_resolver(world: World):
    by_name = {a.name: a.id for a in world.agents}
    folded = {a.name.casefold(): a.id for a in world.agents}
    ids = {a.id for a in world.agents}

    def resolve(name: str) -> Optional[int]:
        text = str(name).strip()
        match = re.fullmatch(r"#(\d+)", text)
        if match:
            value = int(match.group(1))
            return value if value in ids else None
        return by_name.get(text, folded.get(text.casefold()))

    return resolve


def simple_tool_result(name: str) -> dict:
    if name == "send_message_to_user":
        return {"status": "delivered"}
    if name == "send_draft":
        return {"status": "draft_recorded"}
    if name == "wait":
        return {"status": "waiting"}
    return {"status": "error", "error": f"Unknown tool: {name}"}


def parse_agent_id(text: str) -> Optional[int]:
    match = _HASH.search(text or "")
    return int(match.group(1)) if match else None
