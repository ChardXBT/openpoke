"""Per-turn routing context shared between the IA runtime, prompt builder and tools."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

from ...services.execution.registry import get_agent_registry
from ...services.execution.resolver import UNRESOLVED, EventMeta, Resolution, resolve


@dataclass
class TurnContext:
    resolution: Resolution = field(default_factory=lambda: UNRESOLVED)
    event: Optional[EventMeta] = None
    created: set[int] = field(default_factory=set)  # agents created earlier in this IA turn
    enforce: bool = True  # ambiguity contract + spawn checks (evals ablate this)
    show_related: bool = True  # <related_agents> section (evals ablate this)
    message: str = ""  # the user's message this turn (for learning aliases from clarifications)
    challenged: set[int] = field(default_factory=set)  # targets already given confirm_target this turn
    blocked: int = 0  # needs_user_choice answers this turn (escalate the wording on repeats)


current_turn: ContextVar[Optional[TurnContext]] = ContextVar("openpoke_turn_context", default=None)


def build_turn_context(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
    event: Optional[EventMeta] = None,
) -> TurnContext:
    """Resolve the incoming message once per turn. Batched agent results are not resolved."""
    if message_type == "agent" and event is None:
        return TurnContext(event=None)
    resolution = resolve(get_agent_registry(), latest_text, transcript, event)
    return TurnContext(resolution=resolution, event=event, message=latest_text if message_type == "user" else "")
