"""Fill a Poke with months of finished agents, to show it at scale. No model calls, no cost.

    python -m evals.seed_demo --agents 5000

Adds realistic finished agents (searches, bookings, emails that are done) from the eval world
generator to the live registry. They are history only, never open items, so the demo's own
drafts and questions behave exactly as with an empty Poke, while "Open items (2 of 5,002
agents)" shows what the Interaction Agent is not reading on every turn.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from evals.world.generate import build_world
from server.services.execution.registry import AgentRecord, Ref, get_agent_registry, normalize_name


def seed(count: int, seed_value: int = 7) -> int:
    reg = get_agent_registry()
    world = build_world(int(count * 1.4) + 60, seed_value)  # about 3 in 4 filler agents are done
    done = [a for a in world.agents if not a.core and a.state == "done"][:count]
    with reg.connection() as conn:
        next_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM agents").fetchone()[0]) + 1
    taken = {normalize_name(n) for n in reg.names()}
    records, refs = [], {}
    for offset, agent in enumerate(done):
        new_id = next_id + offset
        key = normalize_name(agent.name) or f"agent {new_id}"
        if key in taken:
            key = f"{key} {new_id}"
        taken.add(key)
        records.append(AgentRecord(
            id=new_id, name=agent.name, name_key=key, created_at=agent.created_at,
            last_active_at=agent.last_active_at, state="done", state_note=agent.state_note,
            topic=agent.topic, request_count=1, archived=agent.archived, has_active_trigger=False,
        ))
        if agent.refs:
            refs[new_id] = [Ref(kind, value) for kind, value in agent.refs]
    reg.bulk_insert(records, refs)
    return len(records)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--agents", type=int, default=5000)
    args = parser.parse_args(argv)
    added = seed(args.agents)
    reg = get_agent_registry()
    names = reg.names()
    legacy_tokens = sum(len(n) + 3 for n in names) // 4
    print(f"added {added} finished agents; registry now holds {reg.count()} ({reg.count_open()} open)")
    print(f"OpenPoke today would list all {len(names)} names in every prompt (~{legacy_tokens:,} tokens of names alone);")
    print("this fork shows the open items plus 5 recent ones: at most 31 lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
