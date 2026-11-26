"""Tool definitions for interaction agent."""

import asyncio
import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ...config import get_settings
from ...logging_config import logger
from ...services.conversation import get_conversation_log
from ...services.execution import get_agent_roster, get_execution_agent_logs
from ...services.execution import loops
from ...services.execution.registry import AgentRecord, get_agent_registry, normalize_name
from ...services.execution.resolver import OPEN_STATES, alias_phrase, is_irreversible
from ...services.execution.search import search_agents as _search_agents
from ...services.execution.working_set import format_card
from ...services.profile.store import get_profile_store
from ..execution_agent.batch_manager import ExecutionBatchManager
from .turn_context import TurnContext, current_turn


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False

# Tool schemas for OpenRouter (upstream; used verbatim in legacy mode)
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Deliver instructions to a specific execution agent. Creates a new agent if the name doesn't exist in the roster, or reuses an existing one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Human-readable agent name describing its purpose (e.g., 'Vercel Job Offer', 'Email to Sharanjeet'). This name will be used to identify and potentially reuse the agent."
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                },
                "required": ["agent_name", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Deliver a natural-language response directly to the user. Use this for updates, confirmations, or any assistant response the user should see immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Plain-text message that will be shown to the user and recorded in the conversation log.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": "Record an email draft so the user can review the exact text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email for the draft.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject for the draft.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text).",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when a message is already in conversation history to avoid duplicating responses. Adds a <wait> log entry that is not visible to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why waiting (e.g., 'Message already sent', 'Draft already created').",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]


def _build_reference_schemas() -> list:
    schemas = copy.deepcopy(TOOL_SCHEMAS)
    send = schemas[0]["function"]
    send["description"] = (
        "Deliver instructions to an execution agent. Reuse an existing agent by its #id or exact name; "
        "a new name creates a new agent. The system may answer needs_user_choice, confirm_target, owned_by "
        "or possible_duplicate instead of dispatching (see Agents and Routing)."
    )
    send["parameters"]["properties"]["agent_name"]["description"] = (
        "An existing agent's #id (e.g. '#412') or exact name, or a new specific name in the form "
        "'Person/Org – Topic' (e.g. 'Alice Chen – Q3 deck')."
    )
    send["parameters"]["properties"]["create_new"] = {
        "type": "boolean",
        "description": "Set true only to confirm that this is separate work after a possible_duplicate answer.",
    }
    schemas.insert(1, {
        "type": "function",
        "function": {
            "name": "search_agents",
            "description": (
                "Search all execution agents, including ones not shown in <agents>, by person, email address, "
                "company, topic, subject or invoice/order number. Returns one-line agent cards."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for."},
                    "include_archived": {
                        "type": "boolean",
                        "description": "Also search agents archived after weeks of inactivity.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    })
    schemas += [
        {
            "type": "function",
            "function": {
                "name": "remember_about_user",
                "description": (
                    "Save a lasting fact the USER told you about themself: a person in their life (subject = "
                    "relationship like 'mom' or 'boss', value = name, plus email if given), a preference, a place, "
                    "a routine or a personal fact. A new value for the same subject replaces the old one. Never "
                    "save things that only appear in emails or agent results."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["person", "preference", "place", "routine", "fact"]},
                        "subject": {"type": "string", "description": "Short key, e.g. 'mom', 'diet', 'home city'."},
                        "value": {"type": "string", "description": "The fact itself, in a few words."},
                        "email": {"type": "string", "description": "Email address, for a person, if known."},
                        "until": {"type": "string", "description": "YYYY-MM-DD, only for a temporary fact "
                                  "('in Lisbon until Friday'); it drops out after that day."},
                    },
                    "required": ["kind", "subject", "value"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "forget_about_user",
                "description": "Permanently delete everything saved about a subject when the user asks you to forget it.",
                "parameters": {
                    "type": "object",
                    "properties": {"subject": {"type": "string"}},
                    "required": ["subject"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_about_user",
                "description": "List everything saved about the user (use when they ask what you know about them).",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
    ]
    return schemas


REFERENCE_TOOL_SCHEMAS = _build_reference_schemas()

_EXECUTION_BATCH_MANAGER = ExecutionBatchManager()


def _reference_mode() -> bool:
    return get_settings().router_mode == "reference"


# ------------------------------------------------------------------ dispatch
def _default_dispatch(agent_name: str, instructions: str) -> None:
    turn = current_turn.get()
    batch_id = turn.turn_id if turn is not None else None

    async def _execute_async() -> None:
        try:
            result = await _EXECUTION_BATCH_MANAGER.execute_agent(agent_name, instructions, batch_id=batch_id)
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"Agent '{agent_name}' completed: {status}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Agent '{agent_name}' failed: {str(exc)}")

    loop = asyncio.get_running_loop()  # raises RuntimeError when there is no loop
    loop.create_task(_execute_async())


_dispatcher: Callable[[str, str], None] = _default_dispatch


def set_dispatcher(fn: Optional[Callable[[str, str], None]]) -> None:
    """Replace how agents are launched (tests and evals record instead of running)."""
    global _dispatcher
    _dispatcher = fn or _default_dispatch


# ------------------------------------------------------------------ send_message_to_agent
def _legacy_send_message_to_agent(agent_name: str, instructions: str) -> ToolResult:
    roster = get_agent_roster()
    roster.load()
    existing_agents = set(roster.get_agents())
    is_new = agent_name not in existing_agents

    if is_new:
        roster.add_agent(agent_name)

    get_execution_agent_logs().record_request(agent_name, instructions)

    action = "Created" if is_new else "Reused"
    logger.info(f"{action} agent: {agent_name}")

    try:
        _dispatcher(agent_name, instructions)
    except RuntimeError:
        logger.error("No running event loop available for async execution")
        return ToolResult(success=False, payload={"error": "No event loop available"})

    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent_name": agent_name,
            "new_agent_created": is_new,
        },
    )


_SEND_ACTION = re.compile(r"\b(send|sending|forward|forwarding|reply|respond|execute)\b")
_SCHEDULE_ACTION = re.compile(r"\b(cancel|stop|pause|delete the reminder|remove the reminder|reschedule|move)\b")


def _action_states(instructions: str):
    """Open states an irreversible instruction can apply to (None when unclear)."""
    text = instructions.casefold()
    if _SCHEDULE_ACTION.search(text):
        return {"scheduled", "waiting_on_user"}
    if _SEND_ACTION.search(text):
        return {"waiting_on_user", "waiting_on_others"}  # send a draft, or reply on a thread
    return None
_WORD = re.compile(r"[0-9a-z]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DUP_NAME_OVERLAP = 0.6


def _cards(reg, records: list[AgentRecord]) -> list[str]:
    now = reg.clock()
    return [format_card(reg, r, now) for r in records]


def _name_overlap(a: str, b: str) -> float:
    left, right = set(normalize_name(a).split()), set(normalize_name(b).split())
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _related_to(reg, owner: AgentRecord, agent_name: str, instructions: str) -> bool:
    if _name_overlap(agent_name, owner.name) > 0:
        return True
    emails = {e.casefold() for e in _EMAIL.findall(f"{agent_name} {instructions}")}
    return bool(emails) and any(r.kind == "contact" and r.value in emails for r in reg.refs(owner.id))


def _possible_duplicates(reg, agent_name: str, instructions: str, ctx: TurnContext) -> list[AgentRecord]:
    """Open agents that look like the same work: same name words, or same contact and topic words."""
    emails = {e.casefold() for e in _EMAIL.findall(f"{agent_name} {instructions}")}
    hits = _search_agents(reg, f"{agent_name} {instructions}", include_archived=False, limit=8)
    out: list[AgentRecord] = []
    for hit in hits:
        rec = hit.agent
        if rec.id in ctx.created or rec.state not in OPEN_STATES:
            continue
        same_name = _name_overlap(agent_name, rec.name) >= _DUP_NAME_OVERLAP
        same_contact = bool(emails) and any(
            ref.kind == "contact" and ref.value in emails for ref in reg.refs(rec.id)
        )
        if same_name or (same_contact and _name_overlap(agent_name, rec.name) > 0):
            out.append(rec)
    return out[:3]


def _learn_alias(reg, ctx: TurnContext, target_id: int, created: bool) -> None:
    """After a "which one?" question, the agent the work goes to learns the phrase the user used."""
    if not ctx.message:  # agent results and email events are not the user answering
        return
    pending = reg.pending_choice()
    if not pending or created or target_id not in pending.get("candidates", []):
        return
    phrase = str(pending.get("phrase", ""))
    if alias_phrase(ctx.message) == phrase:  # same ambiguous wording again: not an answer
        return
    if len(phrase.split()) >= 2:
        reg.add_ref(target_id, "alias", phrase)
    reg.set_meta("pending_choice", None)


def _reference_send_message_to_agent(agent_name: str, instructions: str, create_new: bool = False) -> ToolResult:
    reg = get_agent_registry()
    ctx = current_turn.get() or TurnContext()
    res = ctx.resolution
    target = reg.find(agent_name)
    irreversible = is_irreversible(instructions)
    # The model can word "send it" as "proceed with the draft"; when the target is one of the
    # ambiguous candidates, the user's own words decide whether the step is irreversible.
    user_irreversible = bool(ctx.message) and is_irreversible(ctx.message)
    is_send = irreversible and bool(_SEND_ACTION.search(instructions.casefold()))
    if not ctx.enforce:  # ablation: plain reuse-or-create with #id/normalized lookup only
        irreversible = is_send = user_irreversible = False
        create_new = True

    # 1. Ambiguity contract: never guess on a step that cannot be undone. Candidates that
    #    the action cannot apply to (sending something with no draft, cancelling something
    #    with no reminder) are not real alternatives.
    on_candidate = target is not None and target.id in res.candidates
    if res.kind == "ambiguous" and (irreversible or (user_irreversible and on_candidate)) and (
        target is None or target.id not in ctx.created
    ):
        candidates = reg.get_many(res.candidates)
        states = _action_states(instructions) or (_action_states(ctx.message) if ctx.message else None)
        fitting = [c for c in candidates if states is None or c.state in states] or candidates
        if target is not None and [c.id for c in fitting] == [target.id]:
            fitting = []  # the only candidate the action fits is the one chosen: no real ambiguity
    else:
        fitting = []
    if fitting:
        options = fitting
        phrase = alias_phrase(ctx.message)
        if len(phrase.split()) >= 2:  # remember the question, to learn the answer next turn
            reg.set_pending_choice(phrase, [c.id for c in options])
        ctx.blocked += 1
        message = "Ambiguous request and the next step cannot be undone. Ask the user which one; do not pick."
        if ctx.blocked > 1:  # the model is retrying: a turn that never replies is the worst outcome
            message = "Stop retrying tools. Reply to the user now and ask which one they mean."
        return ToolResult(success=False, payload={
            "status": "needs_user_choice",
            "message": message,
            "options": _cards(reg, options),
        })

    # 2. Irreversible step aimed somewhere other than what the message refers to. Soft: the
    #    model may insist once (it saw the suggestion), and only a same-kind target (another
    #    draft when the message meant a draft) counts as a likely wrong send.
    if irreversible and target is not None and target.id not in ctx.created and target.id not in ctx.challenged:
        meant = reg.get(res.candidates[0]) if res.kind == "resolved" and res.candidates else None
        if meant is not None and target.id not in res.candidates and target.state == meant.state:
            ctx.challenged.add(target.id)
            suggested = [meant]
            return ToolResult(success=False, payload={
                "status": "confirm_target",
                "message": "The user's message most likely refers to this agent instead. Re-route, or ask the user.",
                "suggested": _cards(reg, suggested),
            })
        # Only *sending a draft* needs an agent that holds one; forwarding or replying with
        # new content from a finished agent is legitimate.
        sends_draft = is_send and "draft" in instructions.casefold()
        if res.kind == "unresolved" and sends_draft and target.state != "waiting_on_user":
            drafts = [r for r in reg.open_items(25) if r.state == "waiting_on_user"][:3]
            if drafts:
                ctx.challenged.add(target.id)
                return ToolResult(success=False, payload={
                    "status": "confirm_target",
                    "message": "This agent has no draft waiting for approval, but these do. Re-route, or ask the user.",
                    "suggested": _cards(reg, drafts),
                })

    created = False
    if target is None:
        # 3. Work that an existing agent already owns must go to that agent.
        if not create_new and res.kind == "resolved" and res.rule in {"explicit", "event", "reference"}:
            owner = reg.get(res.candidates[0])
            # Only a new agent about the same thing is a duplicate: an email reply on the
            # owner's thread, or shared name words / contact. "send it, and also find
            # flights" must still be able to start the flight search.
            if owner is not None and owner.id not in ctx.created and (
                res.rule == "event" or _related_to(reg, owner, agent_name, instructions)
            ):
                return ToolResult(success=False, payload={
                    "status": "owned_by",
                    "message": "An existing agent already owns this. Send the work to it by #id.",
                    "agent": _cards(reg, [owner])[0],
                })
        # 4. Likely duplicate of open work.
        if not create_new:
            dupes = _possible_duplicates(reg, agent_name, instructions, ctx)
            if dupes:
                return ToolResult(success=False, payload={
                    "status": "possible_duplicate",
                    "message": "These open agents look like the same work. Reuse one by #id, or call again with create_new true.",
                    "candidates": _cards(reg, dupes),
                })
        try:
            target, created = reg.get_or_create(agent_name, topic=instructions)
        except ValueError as exc:
            return ToolResult(success=False, payload={"error": str(exc)})
        if created:
            ctx.created.add(target.id)

    canonical = target.name
    _learn_alias(reg, ctx, target.id, created)
    # Visible as "working now" immediately, so later calls this turn see it as open work.
    loops.mark_running(reg, target.id)
    roster = get_agent_roster()
    roster.load()
    roster.add_agent(canonical)
    get_execution_agent_logs().record_request(canonical, instructions)
    logger.info(f"{'Created' if created else 'Reused'} agent: #{target.id} {canonical}")

    try:
        _dispatcher(canonical, instructions)
    except RuntimeError:
        logger.error("No running event loop available for async execution")
        return ToolResult(success=False, payload={"error": "No event loop available"})

    return ToolResult(success=True, payload={
        "status": "submitted",
        "agent": f"#{target.id} {canonical}",
        "new_agent_created": created,
    })


# Create or reuse execution agent and dispatch instructions asynchronously
def send_message_to_agent(agent_name: str, instructions: str, create_new: bool = False) -> ToolResult:
    """Send instructions to an execution agent."""
    if not _reference_mode():
        return _legacy_send_message_to_agent(agent_name, instructions)
    return _reference_send_message_to_agent(agent_name, instructions, create_new=_as_bool(create_new))


def _as_bool(value: Any) -> bool:
    """Models sometimes send booleans as strings: "false" must not mean True."""
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return bool(value)


def search_agents(query: str, include_archived: bool = False) -> ToolResult:
    """Search every execution agent and return one-line cards."""
    reg = get_agent_registry()
    hits = _search_agents(reg, query, include_archived=_as_bool(include_archived), limit=8)
    now = reg.clock()
    cards = []
    for hit in hits:
        card = format_card(reg, hit.agent, now)
        if hit.hidden_for_contact:
            card += f" (+{hit.hidden_for_contact} more for this contact; search more specifically)"
        cards.append(card)
    return ToolResult(success=True, payload={"agents": cards or ["No matching agents."]})


# Send immediate message to user and record in conversation history
def send_message_to_user(message: str) -> ToolResult:
    """Record a user-visible reply in the conversation log."""
    log = get_conversation_log()
    log.record_reply(message)

    return ToolResult(
        success=True,
        payload={"status": "delivered"},
        user_message=message,
        recorded_reply=True,
    )


# Format and record email draft for user review
def send_draft(
    to: str,
    subject: str,
    body: str,
) -> ToolResult:
    """Record a draft update in the conversation log for the interaction agent."""
    log = get_conversation_log()

    message = f"To: {to}\nSubject: {subject}\n\n{body}"

    log.record_reply(message)
    logger.info(f"Draft recorded for: {to}")

    return ToolResult(
        success=True,
        payload={
            "status": "draft_recorded",
            "to": to,
            "subject": subject,
        },
        recorded_reply=True,
    )


# Record silent wait state to avoid duplicate responses
def wait(reason: str) -> ToolResult:
    """Wait silently and add a wait log entry that is not visible to the user."""
    log = get_conversation_log()

    # Record a dedicated wait entry so the UI knows to ignore it
    log.record_wait(reason)

    return ToolResult(
        success=True,
        payload={
            "status": "waiting",
            "reason": reason,
        },
        recorded_reply=True,
    )


# Return predefined tool schemas for LLM function calling
def get_tool_schemas():
    """Return OpenAI-compatible tool schemas for the active router mode."""
    return REFERENCE_TOOL_SCHEMAS if _reference_mode() else TOOL_SCHEMAS


# Route tool calls to appropriate handlers with argument validation and error handling
def handle_tool_call(name: str, arguments: Any) -> ToolResult:
    """Handle tool calls from interaction agent."""
    try:
        if isinstance(arguments, str):
            args = json.loads(arguments) if arguments.strip() else {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            return ToolResult(success=False, payload={"error": "Invalid arguments format"})

        if name == "send_message_to_agent":
            if not _reference_mode():
                args.pop("create_new", None)
            return send_message_to_agent(**args)
        if name == "search_agents" and _reference_mode():
            return search_agents(**args)
        if name == "remember_about_user" and _reference_mode():
            action = get_profile_store().remember(
                str(args.get("kind", "fact")), str(args.get("subject", "")), str(args.get("value", "")),
                email=args.get("email") or None, source="user", until=args.get("until") or None,
            )
            return ToolResult(success=action != "skip", payload={"status": action})
        if name == "forget_about_user" and _reference_mode():
            return ToolResult(success=True, payload={"deleted": get_profile_store().forget(str(args.get("subject", "")))})
        if name == "list_about_user" and _reference_mode():
            facts = [f.line() for f in get_profile_store().current()]
            return ToolResult(success=True, payload={"facts": facts or ["Nothing saved yet."]})
        if name == "send_message_to_user":
            return send_message_to_user(**args)
        if name == "send_draft":
            return send_draft(**args)
        if name == "wait":
            return wait(**args)

        logger.warning("unexpected tool", extra={"tool": name})
        return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
    except json.JSONDecodeError:
        return ToolResult(success=False, payload={"error": "Invalid JSON"})
    except TypeError as exc:
        return ToolResult(success=False, payload={"error": f"Missing required arguments: {exc}"})
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("tool call failed", extra={"tool": name, "error": str(exc)})
        return ToolResult(success=False, payload={"error": "Failed to execute"})
