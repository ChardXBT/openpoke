"""External-fork adapters, on hand-written fixtures in each fork's file format (no fork data)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals.external import ADAPTERS, ExternalSourceError, check_pinned, factories, load_all, src_root
from evals.external import draggle, kareemelmaaddawy as kareem
from evals.schema import Scenario, World
from evals.setups.b0_names import B0Names
from evals.setups.ours import Ours

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --------------------------------------------------------------------------- KareemElMaaddawy fixture

def _k_agent(aid: str, name: str, role: str = "case", seeded: bool = True) -> dict:
    n = aid.rsplit("-", 1)[1]
    return {"agent_id": aid, "thread_id": f"t-test-{n}", "name": name, "role": role, "seeded": seeded,
            "descriptiveness": "specific",
            "work_log": [{"instructions": f"Look into {name.lower()}.\nReport back.", "result": f"Result for {name}."}]}


def _k_case(case_id: str, label: dict, message: dict, required: list[str], entries: list[dict] | None = None,
            style: str = "explicit", summary: str = "", agreed: bool = True) -> dict:
    times = {a: {"created_at": "2026-10-01 08:00:00", "last_active": "2026-10-10 09:00:00"} for a in required}
    return {
        "case_id": case_id, "split": "test", "required_agents": required, "target_position": "first_third",
        "history": {"type": "summarized" if summary else "short", "summary_text": summary, "last_index": -1,
                    "entries": entries or []},
        "message": {"timestamp": "2026-10-20 12:00:00", **message}, "label": label,
        "meta": {"query_style": style}, "agent_ages": {}, "agent_times": times,
        "provenance": {"cross_label": {"agreed": agreed, "label": {"kind": "CREATE"}}},
    }


USER = {"type": "user", "subtype": "user"}


@pytest.fixture
def kareem_src(tmp_path: Path) -> Path:
    base = tmp_path / "KareemElMaaddawy" / kareem.FIXTURES
    _write_jsonl(base / "agents.test.jsonl", [
        _k_agent("a-test-0001", "Gym Refund Lookup"),
        _k_agent("a-test-0002", "Gym Class Schedule"),
        _k_agent("a-test-0003", "Landlord Repair Email"),
        _k_agent("a-test-0004", "Plumber Quote Search", seeded=False),
        *[_k_agent(f"a-test-02{i:02d}", f"Filler Task {i}", role="filler") for i in range(1, 5)],
    ])
    (base / "rosters.test.json").write_text(json.dumps({
        "filler_order": ["a-test-0203", "a-test-0201", "a-test-0204", "a-test-0202"],
        "filler_age_days": {"a-test-0201": 1.5, "a-test-0202": 30.0, "a-test-0203": 0.5, "a-test-0204": 10.0},
    }), encoding="utf-8")
    history = [
        {"tag": "user_message", "timestamp": "2026-10-10 08:59:00", "payload": "did the gym refund me"},
        {"tag": "poke_reply", "timestamp": "2026-10-10 08:59:05", "payload": "checking"},
        {"tag": "agent_message", "timestamp": "2026-10-10 09:00:00",
         "payload": "[SUCCESS] Gym Refund Lookup: Refund of $40 issued Oct 9."},
        {"tag": "poke_reply", "timestamp": "2026-10-10 09:00:05", "payload": "Yes, $40 on Oct 9."},
    ]
    alert = "Important email watcher notification:\nFrom: Pat Lee <Pat.Lee@example.org>. Subject: Kitchen leak. Body: the plumber comes Friday."
    _write_jsonl(base / "cases.single.test.jsonl", [
        _k_case("s-test-0001", {"kind": "REUSE", "threads": ["t-test-0001"]}, {**USER, "text": "forward that to me"},
                ["a-test-0001", "a-test-0002"], history, style="implicit_follow_up", summary="Earlier: chatter."),
        _k_case("s-test-0002", {"kind": "REUSE_ANY", "threads": ["t-test-0002", "t-test-0001"]},
                {**USER, "text": "what time is the gym thing"}, ["a-test-0001", "a-test-0002"], style="any"),
        _k_case("s-test-0003", {"kind": "CREATE", "new_thread": "t-test-0004"}, {**USER, "text": "get a plumber quote"},
                ["a-test-0003"], style="near_distractor", agreed=False),
        _k_case("s-test-0004", {"kind": "NO_DELEGATION"}, {**USER, "text": "thanks!"}, ["a-test-0003"], style="any"),
        _k_case("s-test-0005", {"kind": "REUSE", "threads": ["t-test-0003"]},
                {"type": "agent", "subtype": "execution_result", "text": "[SUCCESS] Landlord Repair Email: done"},
                ["a-test-0003"]),
        _k_case("s-test-0006", {"kind": "REUSE", "threads": ["t-test-0003"]},
                {"type": "agent", "subtype": "email_alert", "text": alert}, ["a-test-0003"], style="paraphrased"),
        _k_case("s-test-0007", {"kind": "REUSE", "threads": ["t-test-0003"]}, {**USER, "text": "and the other one?"},
                ["a-test-0003"], [{"tag": "agent_message", "timestamp": "2026-10-10 09:00:00",
                                   "payload": "[SUCCESS] Some Other Agent: hello"}]),
        _k_case("s-test-0008", {"kind": "CREATE", "new_thread": "t-test-0003"}, {**USER, "text": "new repair"},
                ["a-test-0003"]),
        _k_case("s-test-0009", {"kind": "REUSE", "threads": ["t-test-0003"]},
                {"type": "agent", "subtype": "email_alert", "text": "Important email watcher notification:\nUPS says hi."},
                ["a-test-0003"]),
    ])
    _write_jsonl(base / "cases.sequence.test.jsonl", [{"case_id": "q-test-001", "turns": [{"turn": 1}, {"turn": 2}]}])
    return tmp_path / "KareemElMaaddawy"


def _by_id(result) -> dict[str, tuple[World, Scenario]]:
    return {s.id: (w, s) for w, ss in result.worlds for s in ss}


def _agent(world: World, agent_id):
    return next(a for a in world.agents if a.id == agent_id)


def test_kareem_loads_mappable_cases_and_counts_the_rest(kareem_src):
    result = kareem.load_report(kareem_src, size=5)
    assert sorted(_by_id(result)) == [f"kareemelmaaddawy-s-test-000{i}" for i in (1, 2, 3, 6)]
    assert dict(result.skipped) == {
        kareem.SKIP_SEQUENCE: 2, kareem.SKIP_NO_DELEGATION: 1, kareem.SKIP_EXECUTION_RESULT: 1,
        kareem.SKIP_HISTORY_AGENT: 1, kareem.SKIP_CREATE_IN_ROSTER: 1, kareem.SKIP_ALERT_NO_SENDER: 1,
    }
    assert result.loaded == 4
    assert result.adjusted["cross-labeler disagreed (their final label kept)"] == 1
    assert kareem.load(kareem_src, size=5) == result.worlds


def test_kareem_roster_is_required_plus_filler_prefix(kareem_src):
    world, scenario = _by_id(kareem.load_report(kareem_src, size=5))["kareemelmaaddawy-s-test-0001"]
    names = sorted(a.name for a in world.agents)
    assert names == sorted(["Gym Refund Lookup", "Gym Class Schedule", "Filler Task 3", "Filler Task 1", "Filler Task 4"])
    assert world.size == len(world.agents) == 5
    assert world.now == "2026-10-20T12:00:00Z"
    filler3 = next(a for a in world.agents if a.name == "Filler Task 3")
    assert filler3.last_active_at == "2026-10-20T00:00:00Z" and not filler3.core  # 0.5 days before now
    target = _agent(world, scenario.gold.agent_id)
    assert target.name == "Gym Refund Lookup" and target.core and target.state == "done"
    assert target.topic == "Look into gym refund lookup. Report back."  # one line
    assert target.state_note == "Result for Gym Refund Lookup."
    assert target.created_at == "2026-10-01T08:00:00Z" and target.last_active_at == "2026-10-10T09:00:00Z"
    # ids follow creation order, so the filler made "earlier" outranks nothing by position
    created = [a.created_at for a in sorted(world.agents, key=lambda a: a.id)]
    assert created == sorted(created)


def test_kareem_history_gold_and_families(kareem_src):
    cases = _by_id(kareem.load_report(kareem_src, size=200))
    world, s1 = cases["kareemelmaaddawy-s-test-0001"]
    assert [t.role for t in s1.history] == ["user", "poke", "agent", "poke"]
    agent_turn = s1.history[2]
    assert agent_turn.agent_id == s1.gold.agent_id and agent_turn.text == "Refund of $40 issued Oct 9."
    assert agent_turn.ts == "2026-10-10T09:00:00Z" and agent_turn.status == "SUCCESS"
    assert (s1.family, s1.split, s1.message, s1.event) == (3, "heldout", "forward that to me", None)

    world2, s2 = cases["kareemelmaaddawy-s-test-0002"]
    assert s2.gold.kind == "agent" and len(s2.gold.acceptable) == 1 and s2.family == 0
    assert {_agent(world2, i).name for i in [s2.gold.agent_id, *s2.gold.acceptable]} == {"Gym Class Schedule", "Gym Refund Lookup"}

    world3, s3 = cases["kareemelmaaddawy-s-test-0003"]
    assert (s3.gold.kind, s3.gold.agent_id, s3.family) == ("NEW", None, 6)
    assert "Plumber Quote Search" not in {a.name for a in world3.agents}
    assert "cross-labeler said CREATE" in s3.note

    world6, s6 = cases["kareemelmaaddawy-s-test-0006"]
    assert s6.message is None and s6.family == 8
    assert (s6.event.thread_id, s6.event.sender, s6.event.subject) == ("", "pat.lee@example.org", "Kitchen leak")
    assert s6.event.summary.startswith("From: Pat Lee")
    assert _agent(world6, s6.gold.agent_id).name == "Landlord Repair Email"


# --------------------------------------------------------------------------- draggle fixture

@pytest.fixture
def draggle_src(tmp_path: Path) -> Path:
    root = tmp_path / "draggle"
    agents = [
        {"name": "Sam lunch draft", "person": "Sam", "topic": "lunch", "desc": "lunch",
         "instructions": ["Draft a lunch invite to Sam"], "summary": "Draft ready; not sent.",
         "last_used_days_ago": 0.5, "has_trigger": False},
        {"name": "Reminder: car wash", "person": "", "topic": "car", "desc": "car",
         "instructions": ["Remind me to wash the car"], "summary": "Reminder set Sunday 9am.",
         "last_used_days_ago": 3, "has_trigger": True},
        {"name": "Twin", "person": "Lee", "topic": "a", "desc": "a", "instructions": ["one"], "summary": "x",
         "last_used_days_ago": 5, "has_trigger": False},
        {"name": "Twin", "person": "Lee", "topic": "b", "desc": "b", "instructions": ["two"], "summary": "y",
         "last_used_days_ago": 6, "has_trigger": False},
    ]
    queries = [
        {"kind": "keyword", "text": "did Sam answer about lunch?", "target": "Sam lunch draft"},
        {"kind": "recency", "text": "send it", "target": "Sam lunch draft"},
        {"kind": "lookalike", "text": "move the car wash", "target": "Reminder: car wash"},
        {"kind": "typo", "text": "anything from Lea?", "target": "Twin"},
        {"kind": "keyword", "text": "the boat?", "target": "Boat repair"},
    ]
    (root / draggle.DATA_FILE).parent.mkdir(parents=True)
    (root / draggle.DATA_FILE).write_text(json.dumps({"what": "fixture", "agents": agents, "queries": queries}), encoding="utf-8")
    return root


def test_draggle_single_world_and_skips(draggle_src):
    result = draggle.load_report(draggle_src)
    assert len(result.worlds) == 1
    world, scenarios = result.worlds[0]
    assert world.size == 4 and world.seed == draggle.SEED
    assert [s.id for s in scenarios] == ["draggle-q001", "draggle-q002", "draggle-q003"]
    assert dict(result.skipped) == {draggle.SKIP_GOLD_NAME_NOT_UNIQUE: 1, draggle.SKIP_GOLD_NOT_IN_ROSTER: 1}
    assert [s.family for s in scenarios] == [1, 0, 2]
    assert all(s.history == [] and s.event is None and s.gold.kind == "agent" for s in scenarios)


def test_draggle_agent_mapping(draggle_src):
    world, scenarios = draggle.load_report(draggle_src).worlds[0]
    by_name = {a.name: a for a in world.agents if a.name != "Twin"}
    sam, wash = by_name["Sam lunch draft"], by_name["Reminder: car wash"]
    assert (sam.state, sam.state_note, sam.topic) == ("done", "Draft ready; not sent.", "Draft a lunch invite to Sam")
    assert sam.refs == [("contact_name", "Sam")] and wash.refs == []
    assert (wash.state, wash.has_active_trigger) == ("scheduled", True)
    assert sam.last_active_at == "2026-11-01T00:00:00Z"  # 0.5 days before the anchor
    assert sam.id == 4 and wash.id == 3  # creation order: oldest first
    assert sam.core and wash.core and not any(a.core for a in world.agents if a.name == "Twin")


# --------------------------------------------------------------------------- shared invariants

@pytest.fixture
def all_loads(kareem_src, draggle_src):
    return [kareem.load_report(kareem_src, size=5), draggle.load_report(draggle_src)]


def _check_invariants(loads) -> None:
    seen_ids: set[str] = set()
    cache_keys: set[tuple[int, int]] = set()
    for result in loads:
        for world, scenarios in result.worlds:
            agent_ids = [a.id for a in world.agents]
            assert sorted(agent_ids) == list(range(1, world.size + 1))
            assert (world.seed, world.size) not in cache_keys  # setups cache registries by (seed, size)
            cache_keys.add((world.seed, world.size))
            assert ISO.match(world.now)
            for agent in world.agents:
                assert ISO.match(agent.created_at) and ISO.match(agent.last_active_at)
                assert agent.created_at <= agent.last_active_at <= world.now
                assert "\n" not in agent.state_note and "\n" not in agent.topic
            for s in scenarios:
                assert s.id not in seen_ids
                seen_ids.add(s.id)
                assert s.split == "heldout" and 0 <= s.family <= 10
                assert (s.message is None) != (s.event is None)
                gold_ids = [i for i in [s.gold.agent_id, *s.gold.acceptable] if i is not None]
                assert all(i in agent_ids for i in gold_ids)
                assert (s.gold.kind == "agent") == bool(gold_ids)
                assert all(t.agent_id in agent_ids for t in s.history if t.role == "agent")


def test_invariants_on_fixtures(all_loads):
    _check_invariants(all_loads)


def test_setups_accept_external_worlds(all_loads, tmp_path):
    """Our setups prepare these worlds offline, and resolve each gold agent's name back to its id."""
    for result in all_loads:
        world, scenarios = result.worlds[0]
        scenario = scenarios[0]
        gold_name = _agent(world, scenario.gold.agent_id).name
        for setup in (B0Names(), Ours()):
            prepared = setup.prepare(world, scenario, tmp_path)
            assert prepared.resolve_target(gold_name) == scenario.gold.agent_id


def test_factories_plug_into_the_runner(all_loads):
    world, scenarios = all_loads[1].worlds[0]
    world_factory, scenario_factory = factories(world, scenarios)
    assert world_factory(world.size, 1) is world
    assert scenario_factory(world) == scenarios


def test_check_pinned(tmp_path):
    check_pinned(tmp_path, "abc")  # not a git checkout: fixture directories pass
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("abc\n", encoding="utf-8")
    check_pinned(tmp_path, "abc")
    with pytest.raises(ExternalSourceError, match="pinned"):
        check_pinned(tmp_path, "def")
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    with pytest.raises(ExternalSourceError, match="cannot resolve"):
        check_pinned(tmp_path, "abc")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "main").write_text("abc\n", encoding="utf-8")
    check_pinned(tmp_path, "abc")


def test_missing_data_file_is_explicit(tmp_path):
    with pytest.raises(ExternalSourceError, match="missing data file"):
        draggle.load_report(tmp_path)
    with pytest.raises(ExternalSourceError, match="missing data file"):
        kareem.load_report(tmp_path)


# --------------------------------------------------------------------------- real clones (optional)

def test_pinned_clones_if_present():
    """Exact counts on the pinned clones; skipped where they are not cloned."""
    root = src_root()
    if not all((root / owner).is_dir() for owner in ADAPTERS.values()):
        pytest.skip(f"external clones not found under {root}")
    loads = load_all(root)
    _check_invariants(loads.values())
    k, d = loads["kareemelmaaddawy"], loads["draggle"]
    assert (k.loaded, len(k.worlds), sum(k.skipped.values())) == (105, 105, 111)
    assert {w.size for w, _ in k.worlds} == {200}
    assert (d.loaded, len(d.worlds), sum(d.skipped.values())) == (72, 1, 0)
    assert d.worlds[0][0].size == 50
