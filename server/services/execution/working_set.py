"""The bounded ``<agents>`` block the Interaction Agent sees on every turn.

Open items first (anything not done: waiting on the user, waiting on a reply,
scheduled, running, failed), then a few recently active agents, then one count
line pointing at ``search_agents``. At most 25 + 5 + 1 lines at any roster size.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional

from .loops import age_label, sweep
from .registry import AgentRecord, AgentRegistry

MAX_OPEN = 25
MAX_RECENT = 5
_DETAIL_LIMIT = 140

STATE_LABELS = {
    "waiting_on_user": "waiting on you",
    "waiting_on_others": "waiting on reply",
    "scheduled": "scheduled",
    "running": "working now",
    "failed": "failed",
    "done": "done",
}


def _trim(text: str, limit: int = _DETAIL_LIMIT) -> str:
    text = " ".join((text or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def format_card(reg: AgentRegistry, rec: AgentRecord, now: datetime) -> str:
    """One line per agent, with HTML-escaped details marked as quoted data."""
    label = STATE_LABELS.get(rec.state, rec.state)
    detail = _trim(rec.state_note or rec.topic)
    status = escape(label, quote=False)
    if detail:
        status += f": <quoted_data>{escape(detail, quote=False)}</quoted_data>"
    if rec.archived:
        status = f"archived, {status}"
    return f"#{rec.id} {escape(rec.name, quote=False)} · {status} · {age_label(rec.last_active_at, now)}"


def build_working_set(reg: AgentRegistry, now: Optional[datetime] = None) -> tuple[str, set[int]]:
    """Return the ``<agents>`` block body and the ids it shows."""
    moment = now or reg.clock()
    sweep(reg, moment)
    open_items = reg.open_items(MAX_OPEN)
    recent = reg.recent(MAX_RECENT, exclude={r.id for r in open_items}, only_closed=True)
    shown = open_items + recent
    if not shown:
        return "None", set()
    lines = [format_card(reg, rec, moment) for rec in shown]
    hidden = reg.count(include_archived=True) - len(shown)
    if hidden > 0:
        lines.append(f"+{hidden:,} more agents not shown. Use search_agents to find older work.")
    return "\n".join(lines), {rec.id for rec in shown}


def render_working_set(reg: AgentRegistry, now: Optional[datetime] = None) -> str:
    return build_working_set(reg, now)[0]


__all__ = ["format_card", "build_working_set", "render_working_set", "STATE_LABELS", "MAX_OPEN", "MAX_RECENT"]
