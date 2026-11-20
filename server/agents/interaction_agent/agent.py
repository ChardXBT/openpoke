"""Interaction agent helpers for prompt construction."""

from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from ...config import get_settings
from ...services.execution import get_agent_roster
from ...services.execution.registry import get_agent_registry
from ...services.execution.resolver import EventMeta, render_hint
from ...services.execution.search import search_agents
from ...services.execution.working_set import build_working_set, format_card
from ...services.profile.store import get_profile_store
from .turn_context import TurnContext, build_turn_context, current_turn

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()
ROUTING_PROMPT = (Path(__file__).parent / "routing_prompt.md").read_text(encoding="utf-8").strip()
ABOUT_USER_PROMPT = (Path(__file__).parent / "about_user_prompt.md").read_text(encoding="utf-8").strip()

# Upstream tells the model to reuse agents even when the name is unrelated, which
# invites wrong reuse once there are many agents. Reference mode replaces it.
_UNRELATED_REUSE = "Don't worry if the agent name is unrelated to the new task if it contains useful context."
_SCOPED_REUSE = (
    "Only reuse an agent whose thread, draft, reminder or person+topic matches the request "
    "(see Agents and Routing below)."
)
MAX_RELATED = 3


def _reference_mode() -> bool:
    return get_settings().router_mode == "reference"


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt() -> str:
    """Return the system prompt: upstream text, plus the routing section in reference mode."""
    if not _reference_mode():
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT.replace(_UNRELATED_REUSE, _SCOPED_REUSE) + "\n\n" + ROUTING_PROMPT + "\n\n" + ABOUT_USER_PROMPT


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
    event: Optional[EventMeta] = None,
    ctx: Optional[TurnContext] = None,
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, the agents view, and the latest turn."""
    sections: List[str] = [_render_conversation_history(transcript)]

    if _reference_mode():
        context = ctx or current_turn.get() or build_turn_context(latest_text, transcript, message_type, event)
        sections.extend(_render_reference_sections(latest_text, message_type, context))
    else:
        sections.append(f"<active_agents>\n{_render_active_agents()}\n</active_agents>")

    sections.append(_render_current_turn(latest_text, message_type))
    return [{"role": "user", "content": "\n\n".join(sections)}]


def _render_reference_sections(latest_text: str, message_type: str, ctx: TurnContext) -> List[str]:
    reg = get_agent_registry()
    now = reg.clock()
    agents_text, shown = build_working_set(reg, now)
    out = [f"<about_user>\n{escape(get_profile_store().render_block(latest_text), quote=False)}\n</about_user>",
           f"<agents>\n{agents_text}\n</agents>"]

    query = latest_text
    if ctx.event is not None:
        query = " ".join(filter(None, [ctx.event.subject, ctx.event.sender, latest_text]))
    if ctx.show_related and (message_type == "user" or ctx.event is not None):
        skip = shown | set(ctx.resolution.candidates)
        related = [
            hit.agent for hit in search_agents(reg, query, include_archived=True, limit=MAX_RELATED + len(skip), now=now)
            if hit.agent.id not in skip
        ][:MAX_RELATED]
        if related:
            out.append("<related_agents>\n" + "\n".join(format_card(reg, r, now) for r in related) + "\n</related_agents>")

    hint = render_hint(reg, ctx.resolution)
    if hint:
        out.append(f"<routing_hint>\n{escape(hint, quote=False)}\n</routing_hint>")
    return out


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Format currently active execution agents into XML tags for LLM awareness (legacy mode)
def _render_active_agents() -> str:
    roster = get_agent_roster()
    roster.load()
    agents = roster.get_agents()

    if not agents:
        return "None"

    rendered: List[str] = []
    for agent_name in agents:
        name = escape(agent_name or "agent", quote=True)
        rendered.append(f'<agent name="{name}" />')

    return "\n".join(rendered)


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
