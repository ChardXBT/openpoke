"""draggle/openpoke: the hand-written held-out routing set ``evals/heldout_routing.json``.

One roster of 50 agents shared by every query, so all cases load into a single World. The file
has no case ids; a query's id is its 1-based position in ``queries``.
"""

from __future__ import annotations

from pathlib import Path

from evals.external import ExternalLoad, check_pinned, days_before, one_line, read_json
from evals.schema import Gold, Scenario, World, WorldAgent

OWNER = "draggle"
ID_PREFIX = "draggle"
PINNED_SHA = "eae649b3bb2260cbcba86a73edbc50b38b99fd86"
DATA_FILE = Path("evals") / "heldout_routing.json"
SEED = 2_000_001  # setups cache by (seed, size); keep clear of our own seeds and other adapters
# Their ages are relative ("last_used_days_ago") with no absolute date; any anchor gives the same ages.
NOW = "2026-11-01T12:00:00Z"

# Their query kind -> our family (spec section 7); anything else is 0.
FAMILY = {"keyword": 1, "topic_only": 1, "paraphrase": 1, "lookalike": 2}

SKIP_GOLD_NOT_IN_ROSTER = "gold agent not in the roster"
SKIP_GOLD_NAME_NOT_UNIQUE = "gold agent name is not unique in the roster"


def load(src_dir: Path) -> list[tuple[World, list[Scenario]]]:
    return load_report(src_dir).worlds


def load_report(src_dir: Path) -> ExternalLoad:
    check_pinned(src_dir, PINNED_SHA)
    data = read_json(src_dir / DATA_FILE)
    agents = _agents(data["agents"])
    world = World(size=len(agents), seed=SEED, now=NOW, contacts=[], agents=agents)
    result = ExternalLoad(owner=OWNER, worlds=[])

    by_name: dict[str, list[int]] = {}
    for agent in agents:
        by_name.setdefault(agent.name, []).append(agent.id)
    scenarios = []
    for position, query in enumerate(data["queries"], start=1):
        ids = by_name.get(query["target"], [])
        if not ids:
            result.skipped[SKIP_GOLD_NOT_IN_ROSTER] += 1
            continue
        if len(ids) > 1:
            result.skipped[SKIP_GOLD_NAME_NOT_UNIQUE] += 1
            continue
        scenarios.append(Scenario(
            id=f"{ID_PREFIX}-q{position:03d}", family=FAMILY.get(query["kind"], 0), split="heldout",
            history=[], message=query["text"], event=None, gold=Gold(kind="agent", agent_id=ids[0]),
            irreversible=False, note=f"their kind {query['kind']}",
        ))
    gold_ids = {s.gold.agent_id for s in scenarios}
    for agent in agents:
        agent.core = agent.id in gold_ids
    if scenarios:
        result.worlds.append((world, scenarios))
    return result


def _agents(specs: list[dict]) -> list[WorldAgent]:
    """Ids follow creation order, as the registry assigns them (oldest last use first)."""
    order = sorted(range(len(specs)), key=lambda i: (-float(specs[i]["last_used_days_ago"]), i))
    agents = []
    for new_id, index in enumerate(order, start=1):
        spec = specs[index]
        last = days_before(NOW, float(spec["last_used_days_ago"]))
        trigger = bool(spec.get("has_trigger"))
        instructions = spec.get("instructions") or [""]
        person = (spec.get("person") or "").strip()
        agents.append(WorldAgent(
            id=new_id, name=spec["name"], topic=one_line(instructions[0]), created_at=last, last_active_at=last,
            state="scheduled" if trigger else "done", state_note=one_line(spec.get("summary", "")),
            refs=[("contact_name", person)] if person else [], has_active_trigger=trigger,
        ))
    return agents
