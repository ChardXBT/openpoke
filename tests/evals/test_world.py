import re
import time
from collections import Counter
from datetime import datetime, timedelta

import pytest

from evals.world import build_world
from evals.world.generate import CORE_AGENT_COUNT, NOW, name_key

_OPEN_STATES = {"running", "waiting_on_user", "waiting_on_others", "scheduled"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def _refs(agent, kind: str) -> list[str]:
    return [value for ref_kind, value in agent.refs if ref_kind == kind]


def test_deterministic():
    assert build_world(500, 1) == build_world(500, 1)
    assert build_world(500, 1).agents != build_world(500, 2).agents


@pytest.mark.parametrize("size", [30, 1000, 20000])
def test_sizes(size):
    world = build_world(size, 1)
    assert world.size == size
    assert len(world.agents) == size
    assert [agent.id for agent in world.agents] == list(range(1, size + 1))
    assert world.now == NOW
    assert 35 <= len(world.contacts) <= 45


def test_core_stable_across_sizes():
    small = build_world(CORE_AGENT_COUNT, 1)
    for size in (CORE_AGENT_COUNT + 1, 1000, 5000):
        world = build_world(size, 1)
        assert world.agents[:CORE_AGENT_COUNT] == small.agents
        assert world.contacts == small.contacts
    assert all(agent.core for agent in small.agents)
    assert not any(agent.core for agent in build_world(200, 1).agents[CORE_AGENT_COUNT:])
    # Filler is also prefix-stable, so a bigger world only appends agents.
    assert build_world(5000, 1).agents[:1000] == build_world(1000, 1).agents


def test_size_below_core_raises():
    with pytest.raises(ValueError):
        build_world(CORE_AGENT_COUNT - 1, 1)
    with pytest.raises(ValueError):
        build_world(0, 1)
    assert CORE_AGENT_COUNT <= 30


def test_twenty_k_under_10s():
    start = time.perf_counter()
    world = build_world(20000, 1)
    assert time.perf_counter() - start < 10
    assert len(world.agents) == 20000


def test_world_invariants():
    world = build_world(5000, 1)
    now = _parse(NOW)
    names = Counter(name_key(agent.name) for agent in world.agents)
    assert names.most_common(1)[0][1] == 1, "agent names must be unique after normalization"
    ids_by_kind: dict[str, set[str]] = {"thread": set(), "draft": set(), "trigger": set()}
    for agent in world.agents:
        for ts in (agent.created_at, agent.last_active_at):
            assert _ISO.match(ts), ts
            assert now - timedelta(days=90) <= _parse(ts) < now
        assert agent.created_at <= agent.last_active_at
        idle = now - _parse(agent.last_active_at)
        assert "\n" not in agent.state_note and agent.state_note
        assert agent.archived == (
            agent.state in ("done", "failed") and idle >= timedelta(days=14) and not agent.has_active_trigger
        )
        assert agent.has_active_trigger == (agent.state == "scheduled")
        if agent.state == "waiting_on_others":
            assert idle < timedelta(days=7)
        if agent.state == "waiting_on_user":
            assert _refs(agent, "draft")
        if agent.state == "scheduled":
            assert _refs(agent, "trigger")
        for email in _refs(agent, "contact"):
            assert email == email.lower() and "@" in email
        for kind, seen in ids_by_kind.items():
            for value in _refs(agent, kind):
                assert value not in seen, f"duplicate {kind} {value}"
                seen.add(value)
        assert all(re.fullmatch(r"thr_[0-9a-f]{4,6}", v) for v in _refs(agent, "thread"))
        assert all(re.fullmatch(r"dr_[0-9a-f]{4,6}", v) for v in _refs(agent, "draft"))
    states = Counter(agent.state for agent in world.agents)
    assert states["done"] > 0.7 * world.size
    for state in ("waiting_on_user", "waiting_on_others", "scheduled", "failed"):
        assert states[state] > 0
    assert sum(agent.archived for agent in world.agents) > 0.5 * world.size


def test_collision_families_present():
    world = build_world(CORE_AGENT_COUNT, 1)
    names = [contact.name for contact in world.contacts]
    assert "Alice Chen" in names and "Alice Romero" in names
    assert any(n.startswith("Alicia") for n in names)
    assert any(n.startswith("Al ") for n in names)
    assert any(n.startswith("Allison") for n in names)
    by_company = Counter(contact.company for contact in world.contacts if contact.company)
    assert by_company["Northwind"] >= 2
    alice = next(c for c in world.contacts if c.name == "Alice Chen")
    alice_open = [a for a in world.agents if ("contact", alice.email) in a.refs and a.state in _OPEN_STATES]
    assert len(alice_open) == 4


def test_cli_prints_counts(capsys):
    from evals.world.generate import main

    main(["--size", "200", "--seed", "1"])
    out = capsys.readouterr().out
    assert "waiting_on_user" in out and "archived" in out
