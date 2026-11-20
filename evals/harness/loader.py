"""Load a synthetic World into the real AgentRegistry (in memory)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from evals.schema import World
from server.services.execution.registry import AgentRecord, AgentRegistry, Ref, normalize_name


def world_now(world: World) -> datetime:
    return datetime.strptime(world.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_world(world: World) -> AgentRegistry:
    """Bulk-insert every agent and ref; the registry's clock is pinned to ``world.now``."""
    now = world_now(world)
    reg = AgentRegistry(Path(":memory:"), clock=lambda: now)
    records, refs = [], {}
    taken: set[str] = set()
    for agent in world.agents:
        key = normalize_name(agent.name) or f"agent {agent.id}"
        if key in taken:  # the registry enforces unique normalized names
            key = f"{key} {agent.id}"
        taken.add(key)
        records.append(AgentRecord(
            id=agent.id, name=agent.name, name_key=key, created_at=agent.created_at,
            last_active_at=agent.last_active_at, state=agent.state, state_note=agent.state_note,
            topic=agent.topic, request_count=1, archived=agent.archived,
            has_active_trigger=agent.has_active_trigger,
        ))
        if agent.refs:
            refs[agent.id] = [Ref(kind, value) for kind, value in agent.refs]
    reg.bulk_insert(records, refs)
    return reg
