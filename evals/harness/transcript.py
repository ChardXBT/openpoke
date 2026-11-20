"""Render eval conversations in OpenPoke's own transcript format.

Matches server/services/conversation/log.py: one XML-like tag per entry with a
user-timezone timestamp ("YYYY-MM-DD HH:MM:SS"; evals use UTC), payload
HTML-escaped. Agent results are recorded the way ExecutionBatchManager formats
them: "[SUCCESS] <agent name>: <response>".
"""

from __future__ import annotations

from html import escape
from typing import Optional

from evals.schema import EmailEvent, Turn, World

_TAGS = {"user": "user_message", "poke": "poke_reply", "agent": "agent_message"}


def _stamp(iso_ts: str) -> str:
    return iso_ts.replace("T", " ").replace("Z", "")[:19]


def render_history(turns: list[Turn], world: World) -> str:
    names = {agent.id: agent.name for agent in world.agents}
    lines: list[str] = []
    for turn in turns:
        tag = _TAGS[turn.role]
        payload = turn.text
        if turn.role == "agent":
            name = names.get(turn.agent_id, f"agent {turn.agent_id}")
            payload = f"[{turn.status}] {name}: {turn.text}"
        lines.append(f'<{tag} timestamp="{_stamp(turn.ts)}">{escape(payload, quote=False)}</{tag}>')
    return "\n".join(lines)


def render_current_turn(message: Optional[str], event: Optional[EmailEvent]) -> tuple[str, str]:
    """Return (latest_text, message_type) as OpenPoke's runtime would receive them."""
    if event is not None:
        return f"Important email watcher notification:\n{event.summary}", "agent"
    return message or "", "user"
