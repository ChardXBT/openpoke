import re
import time
from collections import Counter
from datetime import datetime, timedelta

import pytest

from evals.world import build_scenarios, build_world
from evals.world.generate import CORE_AGENT_COUNT, NOW, RESERVED_TERMS, main, name_key

_OPEN_STATES = {"running", "waiting_on_user", "waiting_on_others", "scheduled"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def _refs(agent, kind: str) -> list[str]:
    return [value for ref_kind, value in agent.refs if ref_kind == kind]


_RESERVED = [{re.compile(r"(?<![a-z0-9])" + re.escape(term)) for term in combo} for combo in RESERVED_TERMS]
_ANY_RESERVED = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(term) for combo in RESERVED_TERMS for term in combo) + ")"
)


def _reserved_hits(agent) -> list[tuple[str, ...]]:
    """Reserved term combos fully present in an agent's name, topic, note or refs."""
    text = " ".join([agent.name, agent.topic, agent.state_note, *(v for _, v in agent.refs)]).casefold()
    if not _ANY_RESERVED.search(text):  # fast path: most filler hits no term at all
        return []
    return [combo for combo, patterns in zip(RESERVED_TERMS, _RESERVED) if all(p.search(text) for p in patterns)]


def _scenario_texts(scenarios) -> set[str]:
    texts: set[str] = set()
    for scenario in scenarios:
        if scenario.message:
            texts.add(scenario.message.strip().casefold())
        if scenario.event:
            texts.add(scenario.event.summary.strip().casefold())
        texts.update(turn.text.strip().casefold() for turn in scenario.history)
    return texts


@pytest.fixture(scope="module")
def dev_world():
    return build_world(1000, 1)


@pytest.fixture(scope="module")
def heldout_world():
    return build_world(1000, 2)


@pytest.fixture(scope="module")
def dev(dev_world):
    return build_scenarios(dev_world)


@pytest.fixture(scope="module")
def heldout(heldout_world):
    return build_scenarios(heldout_world)


def test_deterministic():
    assert build_world(500, 1) == build_world(500, 1)
    assert build_scenarios(build_world(CORE_AGENT_COUNT, 2)) == build_scenarios(build_world(CORE_AGENT_COUNT, 2))
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


def test_gold_agents_exist(dev, heldout, dev_world, heldout_world):
    for world, scenarios in ((dev_world, dev), (heldout_world, heldout)):
        small = build_world(CORE_AGENT_COUNT, world.seed)
        assert build_scenarios(small) == scenarios, "scenarios must depend only on core agents"
        by_id = {agent.id: agent for agent in world.agents}
        for scenario in scenarios:
            gold = scenario.gold
            if gold.kind == "agent":
                assert by_id[gold.agent_id].core
            else:
                assert gold.agent_id is None
            for agent_id in gold.acceptable:
                assert by_id[agent_id].core
            for turn in scenario.history:
                if turn.role == "agent":
                    assert turn.agent_id is not None and by_id[turn.agent_id].core
                else:
                    assert turn.agent_id is None
            if scenario.event is not None:
                assert scenario.message is None and scenario.family == 8
                owner = by_id[gold.agent_id]
                assert owner.state == "waiting_on_others"
                assert scenario.event.thread_id in _refs(owner, "thread")
                assert scenario.event.sender in _refs(owner, "contact")
            else:
                assert scenario.message


def test_family_counts(dev, heldout):
    for scenarios, split, total in ((dev, "dev", (70, 90)), (heldout, "heldout", (25, 35))):
        counts = Counter(scenario.family for scenario in scenarios)
        assert set(counts) == set(range(1, 11)), counts
        assert counts[4] == counts[5]
        assert 3 <= counts[10] <= 5
        assert total[0] <= len(scenarios) <= total[1]
        assert all(scenario.split == split for scenario in scenarios)
        ids = [scenario.id for scenario in scenarios]
        assert len(set(ids)) == len(ids)
        assert all(re.fullmatch(rf"{split}-\d\d-\d\d\d", scenario_id) for scenario_id in ids)


def test_splits_disjoint(dev, heldout):
    assert not _scenario_texts(dev) & _scenario_texts(heldout)


def test_unknown_seed_has_no_split():
    with pytest.raises(ValueError):
        build_scenarios(build_world(CORE_AGENT_COUNT, 3))


def test_family_semantics(dev, heldout, dev_world):
    by_id = {agent.id: agent for agent in dev_world.agents}
    for scenario in dev + heldout:
        assert scenario.note and "\n" not in scenario.note
        if scenario.family == 4:
            assert scenario.gold.kind == "ASK" and scenario.irreversible
        elif scenario.family == 6:
            assert scenario.gold.kind == "NEW"
        else:
            assert scenario.gold.kind == "agent"
        if scenario.family == 1:
            name_words = set(re.findall(r"[a-z0-9]+", by_id[scenario.gold.agent_id].name.casefold()))
            message_words = set(re.findall(r"[a-z0-9]+", scenario.message.casefold()))
            assert not name_words & message_words, (scenario.id, name_words & message_words)
        if scenario.family == 9:
            assert by_id[scenario.gold.agent_id].archived
        if scenario.family == 3:
            agent_turns = [turn for turn in scenario.history if turn.role == "agent"]
            surfaced_open = {t.agent_id for t in agent_turns if by_id[t.agent_id].state in _OPEN_STATES}
            assert surfaced_open == {scenario.gold.agent_id}
            last = max(i for i, t in enumerate(scenario.history) if t.agent_id == scenario.gold.agent_id)
            assert 1 <= len(scenario.history) - 1 - last <= 4
        if scenario.family == 4:
            recent = [t.agent_id for t in scenario.history[-10:] if t.role == "agent"]
            open_recent = {i for i in recent if by_id[i].state in _OPEN_STATES}
            assert len(open_recent) == 2
            assert len({by_id[i].state for i in open_recent}) == 1
        if scenario.family == 10:
            assert any(turn.role == "poke" and "?" in turn.text for turn in scenario.history)


def test_history_consistent_with_world(dev, heldout, dev_world, heldout_world):
    for world, scenarios in ((dev_world, dev), (heldout_world, heldout)):
        by_id = {agent.id: agent for agent in world.agents}
        now = _parse(world.now)
        for scenario in scenarios:
            stamps = [_parse(turn.ts) for turn in scenario.history]
            assert stamps == sorted(stamps) and all(stamp < now for stamp in stamps), scenario.id
            for turn in scenario.history:
                if turn.role != "agent":
                    continue
                agent = by_id[turn.agent_id]
                assert turn.ts == agent.last_active_at, scenario.id
                text = turn.text.casefold()
                if agent.state == "waiting_on_user":
                    assert _refs(agent, "draft")[0] in text and "draft" in text
                elif agent.state == "waiting_on_others":
                    assert _refs(agent, "thread")[0] in text
                elif agent.state == "scheduled":
                    assert _refs(agent, "trigger")[0] in text
                else:
                    note_words = set(re.findall(r"[a-z0-9$]{4,}", agent.state_note.casefold()))
                    assert note_words & set(re.findall(r"[a-z0-9$]{4,}", text)), scenario.id


def test_filler_cannot_break_gold(dev, heldout):
    for seed, scenarios in ((1, dev), (2, heldout)):
        world = build_world(3000, seed)
        by_id = {agent.id: agent for agent in world.agents}
        gold_ids = {s.gold.agent_id for s in scenarios if s.gold.agent_id} | {
            i for s in scenarios for i in s.gold.acceptable
        }
        gold_emails = {email for i in gold_ids for email in _refs(by_id[i], "contact")}
        gold_names = {name.casefold() for i in gold_ids for name in _refs(by_id[i], "contact_name")}
        for agent in world.agents[CORE_AGENT_COUNT:]:
            is_open = agent.state != "done" and not agent.archived
            if is_open:
                assert not gold_emails & set(_refs(agent, "contact")), agent
                assert not gold_names & {n.casefold() for n in _refs(agent, "contact_name")}, agent
            assert not _reserved_hits(agent), (agent.name, _reserved_hits(agent))
    # The matcher is not vacuous: every core topic a scenario routes to is reserved.
    core = build_world(CORE_AGENT_COUNT, 1).agents
    gold_core = {s.gold.agent_id for s in dev if s.gold.agent_id}
    assert all(_reserved_hits(core[i - 1]) for i in gold_core)


def test_cli_prints_counts(capsys):
    main(["--size", "200", "--seed", "1"])
    out = capsys.readouterr().out
    assert "waiting_on_user" in out and "archived" in out
    assert "family 10" in out
