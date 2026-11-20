"""Open-loop state for execution agents, derived from what their tools actually did.

An agent is an *open loop* while something is pending: a draft waiting for the
user's OK (``waiting_on_user``), a sent message waiting for a reply
(``waiting_on_others``), or a scheduled reminder (``scheduled``). Everything else
is ``done`` (or ``failed``). The Interaction Agent's prompt shows open loops, so
its size tracks how busy the user is, not how long they have used the assistant.

Notes are built from structured tool fields only (addresses, subjects, ids),
never from email bodies, because notes are shown to the Interaction Agent on
every turn.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

from ...logging_config import logger
from .registry import AgentRegistry, parse_iso, to_iso

WAITING_ON_OTHERS_TTL = timedelta(days=7)
ARCHIVE_AFTER_IDLE = timedelta(days=14)
_MAX_SUBJECT = 80
_UNSAFE = re.compile(r"[<>\r\n\t]+")

SEND_TOOLS = {"gmail_execute_draft", "gmail_reply_to_thread", "gmail_forward_email"}


def _clean(text: Any, limit: int = _MAX_SUBJECT) -> str:
    value = _UNSAFE.sub(" ", str(text or "")).strip()
    return value[: limit - 1] + "…" if len(value) > limit else value


def _is_failure(result: Any, success: bool) -> bool:
    if not success:
        return True
    if isinstance(result, dict):
        if result.get("error"):
            return True
        if result.get("successful") is False:
            return True
    return False


def _find_values(obj: Any, keys: set[str], depth: int = 5) -> list[str]:
    """Collect scalar values for any of ``keys`` in a nested dict/list payload."""
    found: list[str] = []
    if depth < 0:
        return found
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys and isinstance(value, (str, int)) and str(value).strip():
                found.append(str(value).strip())
            elif isinstance(value, (dict, list)):
                found.extend(_find_values(value, keys, depth - 1))
    elif isinstance(obj, list):
        for item in obj[:20]:
            found.extend(_find_values(item, keys, depth - 1))
    return found


def _first_ref(reg: AgentRegistry, agent_id: int, kind: str) -> str:
    values = [r.value for r in reg.refs(agent_id) if r.kind == kind]
    return values[-1] if values else ""


# ---------------------------------------------------------------- run lifecycle
def _paused_key(agent_id: int) -> str:
    return f"paused_draft:{agent_id}"


def mark_running(reg: AgentRegistry, agent_id: int, count_request: bool = False) -> None:
    """Mark an agent "working now", remembering a draft that is still awaiting the user's OK.

    A follow-up run that neither sends nor deletes the draft ("what does it say?") leaves the
    draft pending; ``finish_run`` puts the agent back to ``waiting_on_user`` instead of ``done``.
    """
    record = reg.get(agent_id)
    if record is not None and record.state == "waiting_on_user":
        reg.set_meta(_paused_key(agent_id), json.dumps({"note": record.state_note}))
    reg.touch(agent_id, state="running", count_request=count_request)


def begin_run(reg: AgentRegistry, name: str, instructions: str) -> int:
    record, _ = reg.get_or_create(name, topic=_clean(instructions, 160))
    mark_running(reg, record.id, count_request=True)
    return record.id


def observe_tool_call(
    reg: AgentRegistry,
    agent_id: int,
    tool: str,
    args: dict,
    result: Any,
    success: bool,
) -> None:
    """Record refs and the resulting open-loop state for one tool call."""
    if _is_failure(result, success):
        return
    args = args if isinstance(args, dict) else {}
    recipient = _clean(args.get("recipient_email"), 120).casefold()

    if tool == "gmail_create_draft":
        subject = _clean(args.get("subject"))
        if recipient:
            reg.add_ref(agent_id, "contact", recipient)
        if subject:
            reg.add_ref(agent_id, "subject", subject)
        for draft_id in _find_values(result, {"draft_id", "id"})[:1]:
            reg.add_ref(agent_id, "draft", draft_id)
        for thread_id in _find_values(result, {"threadId", "thread_id"})[:1]:
            reg.add_ref(agent_id, "thread", thread_id)
        note = f"draft to {recipient or 'recipient'}" + (f' re "{subject}"' if subject else "") + " awaiting your OK"
        reg.set_state(agent_id, "waiting_on_user", note)
        return

    if tool in SEND_TOOLS:
        thread_id = _clean(args.get("thread_id"), 120)
        if recipient:
            reg.add_ref(agent_id, "contact", recipient)
        if thread_id:
            reg.add_ref(agent_id, "thread", thread_id)
        for found in _find_values(result, {"threadId", "thread_id"})[:1]:
            reg.add_ref(agent_id, "thread", found)
        who = recipient or _first_ref(reg, agent_id, "contact")
        subject = _first_ref(reg, agent_id, "subject")
        verb = {"gmail_execute_draft": "sent draft", "gmail_reply_to_thread": "replied",
                "gmail_forward_email": "forwarded"}[tool]
        note = verb + (f" to {who}" if who else "") + (f' re "{subject}"' if subject else "") + "; waiting for reply"
        reg.set_state(agent_id, "waiting_on_others", note)
        return

    if tool == "gmail_delete_draft":
        reg.set_state(agent_id, "done", "draft deleted")
        return

    if tool == "createTrigger":
        for trigger_id in _find_values(result, {"trigger_id", "id"})[:1]:
            reg.add_ref(agent_id, "trigger", trigger_id)
        if str(args.get("status") or "active").lower() == "active":
            when = _clean(args.get("recurrence_rule") or args.get("start_time") or "", 60)
            reg.set_trigger_flag(agent_id, True)
            reg.set_state(agent_id, "scheduled", "reminder scheduled" + (f" ({when})" if when else ""))
        return

    if tool == "updateTrigger":
        status = str(args.get("status") or "").lower()
        if status in {"paused", "completed"}:
            reg.set_trigger_flag(agent_id, False)
            return
        if status == "active":
            reg.set_trigger_flag(agent_id, True)
        when = _clean(args.get("recurrence_rule") or args.get("start_time") or "", 60)
        if when:  # rescheduled: keep the note truthful, it is shown to the IA every turn
            reg.set_trigger_flag(agent_id, True)
            reg.set_state(agent_id, "scheduled", f"reminder scheduled ({when})")


def _default_trigger_lookup(name: str) -> bool:
    try:
        from ..triggers import get_trigger_service

        return any(
            t.status == "active" and t.next_trigger for t in get_trigger_service().list_triggers(agent_name=name)
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"loops: trigger lookup failed for {name}: {exc}")
        return False


def finish_run(
    reg: AgentRegistry,
    agent_id: int,
    success: bool,
    has_active_trigger: Callable[[str], bool] = _default_trigger_lookup,
) -> None:
    record = reg.get(agent_id)
    if record is None:
        return
    paused = reg.get_meta(_paused_key(agent_id))
    reg.set_meta(_paused_key(agent_id), None)
    active = bool(has_active_trigger(record.name))
    reg.set_trigger_flag(agent_id, active)
    if record.state == "running":
        if not success:
            reg.set_state(agent_id, "failed", "last run failed")
        elif paused is not None:  # nothing sent or deleted: the draft still awaits the user's OK
            try:
                note = str(json.loads(paused).get("note") or "")
            except (ValueError, AttributeError):
                note = ""
            reg.set_state(agent_id, "waiting_on_user", note or "draft awaiting your OK")
        elif active:
            reg.set_state(agent_id, "scheduled", record.state_note or "reminder scheduled")
        else:
            reg.set_state(agent_id, "done")
    elif record.state == "scheduled" and not active:
        reg.set_state(agent_id, "done")


def observe_reply(reg: AgentRegistry, thread_id: Optional[str], sender: str = "", subject: str = "") -> list[int]:
    """A reply arrived on a thread an agent was waiting on: that loop is closed.

    Only the thread id (Gmail's own metadata) can close a loop, never the email's text. The agent
    reopens as usual if the user asks it to answer. Returns the agents closed.
    """
    if not thread_id:
        return []
    closed = []
    for rec in reg.owners("thread", thread_id):
        if rec.state == "waiting_on_others":
            who = _clean(sender, 60) or "they"
            reg.set_state(rec.id, "done", f"{who} replied" + (f": {_clean(subject)}" if subject else ""))
            closed.append(rec.id)
    return closed


def abandon_run(reg: AgentRegistry, name: str) -> None:
    """A run that timed out or never started must not stay "working now" forever."""
    record = reg.find(name)
    if record is not None and record.state == "running":
        reg.set_meta(_paused_key(record.id), None)
        reg.set_state(record.id, "failed", "last run failed")


# ---------------------------------------------------------------- housekeeping
def sweep(reg: AgentRegistry, now: Optional[datetime] = None) -> int:
    """Lapse stale ``waiting_on_others`` and archive idle finished agents. Returns rows changed."""
    moment = now or reg.clock()
    lapse_before = to_iso(moment - WAITING_ON_OTHERS_TTL)
    archive_before = to_iso(moment - ARCHIVE_AFTER_IDLE)
    with reg.connection() as conn:
        lapsed = conn.execute(
            "UPDATE agents SET state = 'done' WHERE state = 'waiting_on_others' AND last_active_at < ?",
            (lapse_before,),
        ).rowcount
        archived = conn.execute(
            "UPDATE agents SET archived = 1 WHERE archived = 0 AND state IN ('done', 'failed') "
            "AND has_active_trigger = 0 AND last_active_at < ?",
            (archive_before,),
        ).rowcount
    return int(lapsed) + int(archived)


def age_label(last_active_at: str, now: datetime) -> str:
    """Compact relative age: '5m ago', '2h ago', '3d ago', '6w ago'."""
    try:
        delta = now - parse_iso(last_active_at)
    except ValueError:
        return "unknown"
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 86400 * 21:
        return f"{seconds // 86400}d ago"
    return f"{seconds // (86400 * 7)}w ago"


__all__: Iterable[str] = (
    "begin_run", "mark_running", "abandon_run", "observe_tool_call", "finish_run", "sweep", "age_label", "SEND_TOOLS",
)
