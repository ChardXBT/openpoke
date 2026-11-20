"""Resolve which execution agent a message or event refers to, in code.

Most routing mistakes at scale are reference mistakes: which draft does "send it"
mean, which Alice, is this the old thread or new work. Many of those can be
settled from data the system already has — thread and draft ids, ``#ids``, the
open items it just showed the user, the metadata on an incoming email. This
module settles what it can and says honestly when something is ambiguous.

Rules, first match wins:
1. explicit  — ``#id`` (not "invoice #402"), a thread/draft id, or an exact agent name.
2. event     — an email notification's thread id, else an open item waiting on its sender.
3. reference — short replies ("send it", "did she reply?", "cancel that") matched
               against open items surfaced in the last few transcript entries.
4. entity    — a person named in the message, matched to that person's open items,
               with the message's topic words breaking ties.
Otherwise unresolved: the Interaction Agent decides from the working set and search.

The resolver never raises; on any internal error it returns ``unresolved``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Iterable, Optional

from ...logging_config import logger
from .registry import AgentRecord, AgentRegistry, normalize_name
from .working_set import STATE_LABELS

OPEN_STATES = frozenset({"waiting_on_user", "waiting_on_others", "scheduled", "running", "failed"})
TRANSCRIPT_WINDOW = 8  # entries scanned for surfaced agents

_HASH_ID = re.compile(r"(?<![\w&])#(\d{1,7})\b")
_NUMBER_WORDS = frozenset(
    "invoice order ticket issue pr case po no number item room flight seat apt apartment unit bill "
    "receipt confirmation booking reservation account ref reference".split()
)
_ID_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{4,}$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WORD = re.compile(r"[0-9a-z]+")
_ENTRY = re.compile(r"<(user_message|poke_reply|agent_message)\b[^>]*>(.*?)</\1>", re.S)
_RESULT_LINE = re.compile(r"^\[(SUCCESS|FAILED)\]\s+(.*)$")
_DRAFT_TO = re.compile(r"\bto:\s*([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.I)

_CONFIRM = re.compile(
    r"^(yes|yep|yeah|yup|ok|okay|k|sure|go ahead|do it|send it|ship it|looks good|lgtm|sounds good|"
    r"perfect|great|confirm(ed)?|approve(d)?)\b"
)
_ANAPHOR = re.compile(
    r"\b(it|that|this|them|those|that one|this one|the one|the draft|the email|the reminder|the message)\b"
)
_ACTION = re.compile(
    r"\b(send|cancel|move|reschedule|change|edit|delete|forward|reply|update|push|stop|approve|resend)\b"
)
_STATUS = re.compile(
    r"\b(did|has|have)\b.*\b(reply|replied|respond|responded|answer|answered|get back|got back|"
    r"write back|wrote back|say|said)\b"
    r"|\b(any|anything)\s+(reply|response|update|news|word|new)\b"
    r"|\bheard back\b"
)
_SEND_WORDS = re.compile(r"\b(yes|yep|yeah|yup|ok|okay|sure|go ahead|send|ship|forward|reply|approve|confirm)\b")
_SCHEDULE_WORDS = re.compile(r"\b(cancel|move|reschedule|stop|push|change the time|snooze)\b")

_IRREVERSIBLE = re.compile(
    r"\b(send|sends|sending|forward|forwarding|reply|respond|execute|delete|remove|cancel|"
    r"unsubscribe|pay|purchase|book|accept|decline)\b"
)
_DRAFT_ONLY = re.compile(r"\b(draft|prepare|write|compose)\b")
_HARD_ACTION = re.compile(r"\b(send|forward|delete|cancel|execute|pay)\b")
_NEGATED_SEND = re.compile(r"\b(don'?t|do not|without|never)\s+(send|sending)\b")

_STOP = frozenset(
    "a an and any anything are about as at be been but by can could did do does done for from get got "
    "go has have he her hers him his how i if in into is it its just let me my new no not now of ok on "
    "one or our out please re she so some that the their them then there they this those to too up us "
    "was we what when where which who why will with would yes yep yeah you your back reply replied "
    "send sent tell ask email emails draft drafts message update news works work thing things stuff "
    "still yet today tomorrow week also".split()
)


@dataclass
class EventMeta:
    kind: str
    thread_id: Optional[str] = None
    sender: Optional[str] = None
    subject: Optional[str] = None


@dataclass
class Resolution:
    kind: str  # "resolved" | "ambiguous" | "unresolved"
    candidates: list[int] = field(default_factory=list)
    reason: str = ""
    rule: str = ""


UNRESOLVED = Resolution("unresolved")


# ---------------------------------------------------------------- classifiers
def is_irreversible(instructions: str) -> bool:
    text = (instructions or "").casefold()
    if not _IRREVERSIBLE.search(text) or _NEGATED_SEND.search(text):
        return False
    if _DRAFT_ONLY.search(text) and not _HARD_ACTION.search(text):
        return False
    return True


def is_reference_message(text: str) -> bool:
    lowered = " ".join((text or "").casefold().split())
    if not lowered or len(lowered.split()) > 12:
        return False
    return bool(
        _CONFIRM.search(lowered)
        or _STATUS.search(lowered)
        or (_ANAPHOR.search(lowered) and _ACTION.search(lowered))
    )


def _content_tokens(text: str) -> list[str]:
    out: list[str] = []
    for token in _WORD.findall((text or "").casefold()):
        if len(token) >= 3 and token not in _STOP and token not in out:
            out.append(token)
    return out


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 4 and token.endswith("s") else token


# ---------------------------------------------------------------- transcript
def _find_agent_by_result_line(reg: AgentRegistry, rest: str) -> Optional[AgentRecord]:
    start = 0
    while True:
        idx = rest.find(": ", start)
        candidate = rest if idx < 0 else rest[:idx]
        rec = reg.find(candidate.strip())
        if rec is not None:
            return rec
        if idx < 0:
            return None
        start = idx + 2


def surfaced_agents(reg: AgentRegistry, transcript: str, window: int = TRANSCRIPT_WINDOW) -> list[int]:
    """Agent ids surfaced in the last ``window`` transcript entries, newest first.

    Agent results ("[SUCCESS] <name>: ...") name their agent; Poke's draft previews
    ("To: alice@acme.com") point at the agent holding a draft for that address.
    """
    entries = _ENTRY.findall(transcript or "")[-window:]
    found: list[int] = []
    for tag, payload in reversed(entries):
        text = unescape(payload)
        if tag == "agent_message":
            batch: list[int] = []
            for line in text.splitlines():
                match = _RESULT_LINE.match(line.strip())
                if not match:
                    continue
                rec = _find_agent_by_result_line(reg, match.group(2))
                if rec is not None and rec.id not in batch:
                    batch.append(rec.id)
            # Within one batch, later lines are not "newer"; keep the batch order reversed
            # so the newest entry's agents come first and earlier batches follow.
            for agent_id in reversed(batch):
                if agent_id not in found:
                    found.append(agent_id)
        elif tag == "poke_reply":
            for email in _DRAFT_TO.findall(text):
                for rec in reg.owners("contact", email, include_archived=False):
                    if rec.state == "waiting_on_user" and rec.id not in found:
                        found.append(rec.id)
    return found


# ---------------------------------------------------------------- helpers
def _single_or_ambiguous(ids: list[int], reason: str, rule: str) -> Resolution:
    unique = list(dict.fromkeys(ids))
    if not unique:
        return UNRESOLVED
    if len(unique) == 1:
        return Resolution("resolved", unique, reason, rule)
    return Resolution("ambiguous", unique, reason, rule)


def _agent_words(reg: AgentRegistry, rec: AgentRecord) -> set[str]:
    words = set(_WORD.findall(f"{rec.name} {rec.topic} {rec.state_note}".casefold()))
    for ref in reg.refs(rec.id):
        if ref.kind in {"contact", "contact_name", "subject", "alias"}:
            words.update(_WORD.findall(ref.value.casefold()))
    return {_stem(w) for w in words if len(w) >= 3}


def _narrow_by_detail(reg: AgentRegistry, ids: list[int], text: str, exclude: Iterable[str] = ()) -> list[int]:
    """Keep the single candidate whose words best match the message's content words, if unique."""
    skip = set(exclude)
    tokens = [_stem(t) for t in _content_tokens(text) if t not in skip]
    if len(ids) < 2 or not tokens:
        return ids
    scores = []
    for rec in reg.get_many(ids):
        words = _agent_words(reg, rec)
        scores.append((sum(1 for t in tokens if t in words), rec.id))
    scores.sort(reverse=True)
    if scores and scores[0][0] > 0 and (len(scores) == 1 or scores[0][0] > scores[1][0]):
        return [scores[0][1]]
    return ids


def _explicit(reg: AgentRegistry, text: str) -> Resolution:
    ids: list[int] = []
    for match in _HASH_ID.finditer(text):
        before = re.findall(r"[a-z]+", text[: match.start()].casefold())
        if before and before[-1] in _NUMBER_WORDS:
            continue
        rec = reg.get(int(match.group(1)))
        if rec is not None:
            ids.append(rec.id)
    for raw in text.split():
        token = raw.strip(".,;:!?()[]{}\"'")
        if _ID_LIKE.match(token) and any(ch.isdigit() for ch in token):
            for kind in ("thread", "draft"):
                ids.extend(rec.id for rec in reg.owners(kind, token))
    if not ids and len(text) <= 160:
        rec = reg.find(text)
        if rec is not None and normalize_name(text) == rec.name_key:
            ids.append(rec.id)
    return _single_or_ambiguous(ids, "the message names it directly", "explicit")


def _event(reg: AgentRegistry, event: EventMeta) -> Resolution:
    if event.thread_id:
        owners = reg.owners("thread", event.thread_id)
        if owners:
            return Resolution(
                "resolved", [owners[0].id], f"the incoming email is on its thread {event.thread_id}", "event"
            )
    if event.sender:
        waiting = [
            r for r in reg.owners("contact", event.sender, include_archived=False)
            if r.state in {"waiting_on_others", "waiting_on_user"}
        ]
        prefer = [r for r in waiting if r.state == "waiting_on_others"] or waiting
        if prefer:
            return _single_or_ambiguous(
                [r.id for r in prefer], f"it is waiting on {event.sender}, who just emailed", "event"
            )
    return UNRESOLVED


def _reference(reg: AgentRegistry, text: str, transcript: str) -> Resolution:
    lowered = text.casefold()
    surfaced = reg.get_many(surfaced_agents(reg, transcript))
    if not surfaced:
        return UNRESOLVED
    if _STATUS.search(lowered):
        wanted, why = {"waiting_on_others"}, "a status question about the reply Poke is waiting on"
    elif _SCHEDULE_WORDS.search(lowered):
        wanted, why = {"scheduled", "waiting_on_user"}, "refers to the item just discussed"
    elif _SEND_WORDS.search(lowered):
        wanted, why = {"waiting_on_user"}, "confirms the draft Poke just showed"
    else:
        wanted, why = set(OPEN_STATES), "refers to the item just discussed"
    ids = [r.id for r in surfaced if r.state in wanted]
    if not ids:
        ids = [r.id for r in surfaced if r.state in OPEN_STATES]
    if not ids:
        return UNRESOLVED
    ids = _narrow_by_detail(reg, ids, text)
    return _single_or_ambiguous(ids, why, "reference")


def _contacts_named(reg: AgentRegistry, text: str) -> tuple[list[AgentRecord], list[str]]:
    agents: dict[int, AgentRecord] = {}
    for email in _EMAIL.findall(text):
        for rec in reg.owners("contact", email):
            agents.setdefault(rec.id, rec)
    used: list[str] = []
    tokens = _content_tokens(_EMAIL.sub(" ", text))[:8]
    if tokens:
        with reg.connection() as conn:
            for token in tokens:
                rows = conn.execute(
                    "SELECT DISTINCT agent_id FROM agent_refs WHERE "
                    "(kind = 'contact_name' AND (value = ? OR value LIKE ? OR value LIKE ? OR value LIKE ?)) OR "
                    "(kind = 'contact' AND (value LIKE ? OR value LIKE ? OR value LIKE ?))",
                    (token, f"{token} %", f"% {token}", f"% {token} %",
                     f"{token}.%@%", f"{token}@%", f"%.{token}@%"),
                ).fetchall()
                if rows:
                    used.append(token)
                    ids = [int(r["agent_id"]) for r in rows]
                    for rec in reg.get_many(ids):
                        agents.setdefault(rec.id, rec)
    return list(agents.values()), used


def _entity(reg: AgentRegistry, text: str) -> Resolution:
    agents, name_tokens = _contacts_named(reg, text)
    open_agents = [r for r in agents if not r.archived and r.state in OPEN_STATES]
    if not open_agents:
        return UNRESOLVED
    open_agents.sort(key=lambda r: r.last_active_at, reverse=True)
    ids = _narrow_by_detail(reg, [r.id for r in open_agents], text, exclude=name_tokens)
    who = ", ".join(name_tokens) or "the person named"
    reason = f"the only open item for {who}" if len(ids) == 1 and len(open_agents) == 1 else f"open item for {who}"
    return _single_or_ambiguous(ids, reason, "entity")


# ---------------------------------------------------------------- entry points
def resolve(
    reg: AgentRegistry,
    text: str,
    transcript: str = "",
    event: Optional[EventMeta] = None,
) -> Resolution:
    try:
        if event is not None:
            res = _event(reg, event)
            if res.kind != "unresolved":
                return res
        message = (text or "").strip()
        if not message:
            return UNRESOLVED
        res = _explicit(reg, message)
        if res.kind != "unresolved":
            return res
        if is_reference_message(message):
            res = _reference(reg, message, transcript)
            if res.kind != "unresolved":
                return res
        return _entity(reg, message)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"resolver: falling back to unresolved: {exc}")
        return UNRESOLVED


def _short(rec: AgentRecord) -> str:
    label = STATE_LABELS.get(rec.state, rec.state)
    note = f": {rec.state_note}" if rec.state_note and rec.state != "done" else ""
    archived = "archived, " if rec.archived else ""
    return f"#{rec.id} {rec.name} ({archived}{label}{note})"


def render_hint(reg: AgentRegistry, res: Resolution) -> str:
    if res.kind == "unresolved" or not res.candidates:
        return ""
    recs = reg.get_many(res.candidates)
    if not recs:
        return ""
    if res.kind == "resolved":
        return f"Likely about {_short(recs[0])}. Why: {res.reason}. Reuse this agent for it."
    options = "; ".join(_short(r) for r in recs[:4])
    return (
        f"Ambiguous: this could be {options}. Before anything is sent, replied, forwarded, deleted or "
        "cancelled, ask the user which one in one short question."
    )


__all__ = [
    "EventMeta", "Resolution", "resolve", "render_hint", "surfaced_agents",
    "is_irreversible", "is_reference_message", "OPEN_STATES",
]
