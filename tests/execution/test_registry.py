import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.registry import AgentRegistry, normalize_name


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def reg(tmp_path, clock) -> AgentRegistry:
    return AgentRegistry(tmp_path / "agents.db", clock=clock)


def test_normalize_name():
    assert normalize_name("  Email to Alice! ") == "email to alice"
    assert normalize_name("Email--to__ALICE") == "email to alice"
    assert normalize_name("🍣 Tokyo") == "tokyo"


def test_get_or_create_normalizes(reg):
    first, created = reg.get_or_create("Email to Alice", topic="Draft the Q3 deck email")
    again, created_again = reg.get_or_create("email  to alice!")
    assert created is True and created_again is False
    assert again.id == first.id
    assert again.name == "Email to Alice"
    assert again.topic == "Draft the Q3 deck email"


def test_find_by_hash_id_and_name(reg):
    rec, _ = reg.get_or_create("Email to Alice")
    assert reg.find(f"#{rec.id}").id == rec.id
    assert reg.find("Email to Alice").id == rec.id
    assert reg.find("EMAIL TO ALICE").id == rec.id
    assert reg.find("#999") is None
    assert reg.find("") is None
    # Models often append the name to the id.
    assert reg.find(f"#{rec.id} Email to Alice").id == rec.id
    assert reg.find(f"#{rec.id} - something else").id == rec.id
    assert reg.find("#999 Email to Alice").id == rec.id  # unknown id falls back to the name


def test_migrates_legacy_roster(tmp_path, clock):
    legacy = tmp_path / "roster.json"
    legacy.write_text(json.dumps(["A", "B", "a"]), encoding="utf-8")
    reg = AgentRegistry(tmp_path / "agents.db", legacy_roster_path=legacy, clock=clock)
    assert reg.names() == ["A", "B"]
    # Re-opening does not migrate twice.
    reg2 = AgentRegistry(tmp_path / "agents.db", legacy_roster_path=legacy, clock=clock)
    assert reg2.count() == 2


def test_corrupt_legacy_roster_is_left_alone(tmp_path, clock):
    legacy = tmp_path / "roster.json"
    legacy.write_text("{not json", encoding="utf-8")
    reg = AgentRegistry(tmp_path / "agents.db", legacy_roster_path=legacy, clock=clock)
    assert reg.count() == 0
    assert legacy.read_text(encoding="utf-8") == "{not json"


def test_odd_names_round_trip(reg):
    names = ['He said "hi" <b>&', "🍣 Tokyo ramen", "x" * 300]
    for name in names:
        rec, _ = reg.get_or_create(name)
        assert reg.find(name).name == name
        assert reg.get(rec.id).name == name


def test_refs_and_owners(reg):
    rec, _ = reg.get_or_create("Email to Alice")
    reg.add_ref(rec.id, "contact", "Alice@Acme.com")
    reg.add_ref(rec.id, "contact", "alice@acme.com")
    reg.add_ref(rec.id, "thread", "thr_8F2a")
    reg.add_ref(rec.id, "subject", "")  # empty values are ignored
    refs = reg.refs(rec.id)
    assert [(r.kind, r.value) for r in refs] == [("contact", "alice@acme.com"), ("thread", "thr_8F2a")]
    assert [a.id for a in reg.owners("contact", "ALICE@acme.com")] == [rec.id]
    assert [a.id for a in reg.owners("thread", "thr_8F2a")] == [rec.id]
    assert reg.owners("thread", "thr_8f2a") == []  # ids are case-sensitive


def test_open_items_and_recent(reg, clock):
    ids = {}
    for name, state in [("a", "done"), ("b", "waiting_on_user"), ("c", "scheduled"), ("d", "failed")]:
        rec, _ = reg.get_or_create(name)
        clock.advance(minutes=1)
        reg.touch(rec.id, state=state)
        ids[name] = rec.id
    archived, _ = reg.get_or_create("e")
    reg.touch(archived.id, state="waiting_on_others")
    reg.set_archived(archived.id, True)

    assert [r.name for r in reg.open_items()] == ["d", "c", "b"]
    assert [r.name for r in reg.recent(limit=2, exclude={ids["d"]})] == ["c", "b"]
    assert reg.count() == 5
    assert reg.count(include_archived=False) == 4


def test_touch_unarchives_and_counts(reg, clock):
    rec, _ = reg.get_or_create("Tokyo ramen search")
    reg.set_archived(rec.id, True)
    clock.advance(hours=2)
    reg.touch(rec.id, state="running", note="looking again", count_request=True)
    got = reg.get(rec.id)
    assert got.archived is False
    assert got.state == "running"
    assert got.state_note == "looking again"
    assert got.request_count == 1
    assert got.last_active_at == "2026-11-20T17:00:00Z"


def test_trigger_flag(reg):
    rec, _ = reg.get_or_create("Gym reminder")
    reg.set_trigger_flag(rec.id, True)
    assert reg.get(rec.id).has_active_trigger is True
    reg.set_trigger_flag(rec.id, False)
    assert reg.get(rec.id).has_active_trigger is False


def test_parallel_writes(reg):
    rec, _ = reg.get_or_create("busy agent")
    errors = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                reg.touch(rec.id, count_request=True)
                reg.add_ref(rec.id, "subject", f"s{n}-{i}")
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert reg.get(rec.id).request_count == 400
    assert len(reg.refs(rec.id)) == 400


def test_bulk_insert_and_fts_sync(reg):
    from server.services.execution.registry import AgentRecord, Ref

    records = [
        AgentRecord(
            id=i, name=f"Agent {i}", name_key=normalize_name(f"Agent {i}"),
            created_at="2026-11-01T00:00:00Z", last_active_at="2026-11-02T00:00:00Z",
            state="done", state_note="", topic=f"topic {i}", request_count=1,
            archived=False, has_active_trigger=False,
        )
        for i in range(1, 501)
    ]
    reg.bulk_insert(records, {7: [Ref("contact", "Bob@Example.com")]})
    assert reg.count() == 500
    assert reg.find("#7").name == "Agent 7"
    assert [a.id for a in reg.owners("contact", "bob@example.com")] == [7]
    with reg.connection() as conn:
        hits = conn.execute("SELECT rowid FROM agents_fts WHERE agents_fts MATCH 'bob'").fetchall()
    assert [h[0] for h in hits] == [7]
