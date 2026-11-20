"""KareemElMaaddawy/openpoke-enhanced: the frozen v1 single-turn test split.

Data: ``evals/agent_overload/fixtures/v1/{agents,cases.single,cases.sequence}.test.jsonl`` and
``rosters.test.json``. Each case gets its own World, built with their documented roster rule
(docs/agent-overload-experiment-design.md, "Building the roster for (case, N)"): the case's
``required_agents`` plus the first ``N - len(required)`` ids of ``filler_order``.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

from evals.external import (
    ExternalLoad, check_pinned, days_before, iso_utc, one_line, read_json, read_jsonl,
)
from evals.schema import EmailEvent, Gold, Scenario, Turn, World, WorldAgent

OWNER = "KareemElMaaddawy"
ID_PREFIX = "kareemelmaaddawy"
PINNED_SHA = "372b47191303bbe6585075ff77ff4ae113601344"
FIXTURES = Path("evals") / "agent_overload" / "fixtures" / "v1"
DEFAULT_SIZE = 200  # their largest roster size
SEED_BASE = 1_000_000  # World.seed = SEED_BASE + case position; setups cache by (seed, size)

ALERT_PREFIX = "Important email watcher notification:\n"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SUBJECT_QUOTED = re.compile(r"subject:?\s*'([^']+)'", re.IGNORECASE)
_SUBJECT_LABELLED = re.compile(r"Subject:\s*(.+?)\.\s")
_STATUS = re.compile(r"^\[(SUCCESS|FAILED)\] ")

# (label kind, their query_style) -> our family (spec section 7); anything else is 0.
FAMILY = {
    ("REUSE", "explicit"): 1, ("REUSE", "paraphrased"): 1, ("REUSE", "implicit_follow_up"): 3,
    ("REUSE", "near_distractor"): 5, ("CREATE", "plain"): 6, ("CREATE", "near_distractor"): 6,
}
INBOUND_FAMILY = 8

SKIP_NO_DELEGATION = "gold NO_DELEGATION (answer directly; no Gold kind for it)"
SKIP_EXECUTION_RESULT = "message is an execution-agent result (not a user message or email event)"
SKIP_ALERT_NO_SENDER = "email alert without a sender address"
SKIP_SEQUENCE = "sequence turn (state carried across turns)"
SKIP_GOLD_NOT_IN_ROSTER = "gold agent not in the case roster"
SKIP_CREATE_IN_ROSTER = "CREATE thread already has an agent in the roster"
SKIP_HISTORY_AGENT = "history names an agent not in the roster"
SKIP_HISTORY_TAG = "history entry with an unknown tag"
SKIP_MISSING_AGENT = "required agent missing from agents file"
SKIP_MISSING_TIMES = "required agent has no agent_times"


class _Skip(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def load(src_dir: Path, size: int = DEFAULT_SIZE) -> list[tuple[World, list[Scenario]]]:
    return load_report(src_dir, size).worlds


def load_report(src_dir: Path, size: int = DEFAULT_SIZE, split: str = "test") -> ExternalLoad:
    check_pinned(src_dir, PINNED_SHA)
    base = src_dir / FIXTURES
    agents = {a["agent_id"]: a for a in read_jsonl(base / f"agents.{split}.jsonl")}
    roster = read_json(base / f"rosters.{split}.json")
    cases = read_jsonl(base / f"cases.single.{split}.jsonl")
    result = ExternalLoad(owner=OWNER, worlds=[])

    sequences = base / f"cases.sequence.{split}.jsonl"
    if sequences.exists():
        result.skipped[SKIP_SEQUENCE] += sum(len(seq.get("turns", [])) for seq in read_jsonl(sequences))

    thread_agent = {a["thread_id"]: a["agent_id"] for a in agents.values()}
    for position, case in enumerate(cases, start=1):
        try:
            world, scenario, adjusted = _case(case, position, agents, thread_agent, roster, size)
        except _Skip as skip:
            result.skipped[skip.reason] += 1
            continue
        result.worlds.append((world, [scenario]))
        result.adjusted.update(adjusted)
    return result


def _case(case: dict, position: int, agents: dict, thread_agent: dict, roster: dict, size: int):
    label = case["label"]
    if label["kind"] == "NO_DELEGATION":
        raise _Skip(SKIP_NO_DELEGATION)
    adjusted: Counter = Counter()
    message, event = _message(case["message"], adjusted)
    now = iso_utc(case["message"]["timestamp"])

    world_agents = _roster(case, agents, roster, size, now)
    by_source = {source: agent for source, agent in world_agents}
    world = World(size=len(world_agents), seed=SEED_BASE + position, now=now, contacts=[],
                  agents=[agent for _, agent in world_agents])

    gold = _gold(label, thread_agent, by_source)
    history = _history(case["history"], world)
    if case["history"].get("summary_text"):
        adjusted["summarized history: their summary_text dropped, entries kept"] += 1

    style = case["meta"].get("query_style")
    family = INBOUND_FAMILY if event is not None else FAMILY.get((label["kind"], style), 0)
    note = f"their label {label['kind']}, query_style {style}"
    cross = (case.get("provenance") or {}).get("cross_label") or {}
    if cross and not cross.get("agreed", True):
        adjusted["cross-labeler disagreed (their final label kept)"] += 1
        note += f"; their cross-labeler said {cross.get('label', {}).get('kind')}, final label kept"
    scenario = Scenario(
        id=f"{ID_PREFIX}-{case['case_id']}", family=family, split="heldout", history=history,
        message=message, event=event, gold=gold, irreversible=False, note=note,
    )
    return world, scenario, adjusted


def _message(msg: dict, adjusted: Counter) -> tuple[Optional[str], Optional[EmailEvent]]:
    if msg["type"] == "user":
        return msg["text"], None
    if msg.get("subtype") == "email_alert":
        text = msg["text"]
        body = text[len(ALERT_PREFIX):] if text.startswith(ALERT_PREFIX) else text
        sender = _EMAIL.search(body)
        if sender is None:
            raise _Skip(SKIP_ALERT_NO_SENDER)
        subject = _SUBJECT_QUOTED.search(body) or _SUBJECT_LABELLED.search(body)
        adjusted["email alert carries no thread id (thread_id left empty)"] += 1
        return None, EmailEvent(thread_id="", sender=sender.group(0).lower(),
                                subject=subject.group(1).strip() if subject else "", summary=body.strip())
    raise _Skip(SKIP_EXECUTION_RESULT)


def _roster(case: dict, agents: dict, roster: dict, size: int, now: str) -> list[tuple[str, WorldAgent]]:
    """(their agent_id, WorldAgent) pairs; ids follow creation order, as the registry assigns them."""
    required = list(case["required_agents"])
    filler = [a for a in roster["filler_order"] if a not in required][: max(0, size - len(required))]
    entries = []
    for index, source in enumerate(required + filler):
        spec = agents.get(source)
        if spec is None:
            raise _Skip(SKIP_MISSING_AGENT)
        if source in required:
            times = case.get("agent_times", {}).get(source)
            if times is None:
                raise _Skip(SKIP_MISSING_TIMES)
            last, created = iso_utc(times["last_active"]), iso_utc(times["created_at"])
        else:
            last = days_before(now, float(roster["filler_age_days"][source]))
            created = last  # their per-agent created_at offset for filler is generated in code, not stored
        entries.append((created, last, index, source, spec))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    out = []
    for new_id, (created, last, _, source, spec) in enumerate(entries, start=1):
        log = spec.get("work_log") or [{}]
        out.append((source, WorldAgent(
            id=new_id, name=spec["name"], topic=one_line(log[0].get("instructions", "")),
            created_at=created, last_active_at=last, state="done",
            state_note=one_line(log[-1].get("result", "")), core=source in required,
        )))
    return out


def _gold(label: dict, thread_agent: dict, by_source: dict) -> Gold:
    kind = label["kind"]
    if kind == "CREATE":
        existing = thread_agent.get(label.get("new_thread"))
        if existing in by_source:
            raise _Skip(SKIP_CREATE_IN_ROSTER)
        return Gold(kind="NEW")
    ids = []
    for thread in label.get("threads", []):
        agent = by_source.get(thread_agent.get(thread))
        if agent is None:
            raise _Skip(SKIP_GOLD_NOT_IN_ROSTER)
        ids.append(agent.id)
    if not ids:
        raise _Skip(SKIP_GOLD_NOT_IN_ROSTER)
    return Gold(kind="agent", agent_id=ids[0], acceptable=ids[1:])


def _history(history: dict, world: World) -> list[Turn]:
    names = sorted(world.agents, key=lambda a: len(a.name), reverse=True)
    roles = {"user_message": "user", "poke_reply": "poke"}
    turns = []
    for entry in history.get("entries", []):
        ts, payload = iso_utc(entry["timestamp"]), entry["payload"]
        if entry["tag"] in roles:
            turns.append(Turn(role=roles[entry["tag"]], text=payload, ts=ts))
            continue
        if entry["tag"] != "agent_message":
            raise _Skip(SKIP_HISTORY_TAG)
        status = _STATUS.match(payload)
        rest = payload[status.end():] if status else ""
        agent = next((a for a in names if rest.startswith(f"{a.name}: ")), None)
        if status is None or agent is None:
            raise _Skip(SKIP_HISTORY_AGENT)
        turns.append(Turn(role="agent", text=rest[len(agent.name) + 2:], ts=ts, agent_id=agent.id,
                          status=status.group(1)))
    return turns
