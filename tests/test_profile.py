from datetime import datetime, timedelta, timezone

import pytest

from server.services.profile.store import ProfileStore


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def store(tmp_path, clock):
    return ProfileStore(tmp_path / "profile.db", clock=clock)


def test_add_noop_update_keeps_history(store, clock):
    assert store.remember("place", "home city", "Vancouver") == "add"
    assert store.remember("place", "Home City", "vancouver") == "noop"  # same fact, different casing
    clock.now += timedelta(days=30)
    assert store.remember("place", "home city", "Toronto") == "update"
    current = store.current()
    assert [(f.subject, f.value) for f in current] == [("home city", "Toronto")]
    history = store.history("home city")
    assert [h.value for h in history] == ["Vancouver", "Toronto"]
    assert history[0].invalid_at == "2026-12-20T15:00:00Z"


def test_person_with_email_and_lookup(store):
    store.remember("person", "mom", "Linda Song", email="Linda.Song@gmail.com")
    store.remember("person", "my dentist", "Dr. Patel", email="frontdesk@smiles.example")
    mom = store.person("mom")
    assert mom.value == "Linda Song" and mom.email == "linda.song@gmail.com"
    assert store.person("dentist").email == "frontdesk@smiles.example"  # "my" is ignored
    assert store.person("dad") is None


def test_forget_deletes_everything_about_a_subject(store, clock):
    store.remember("place", "home city", "Vancouver")
    clock.now += timedelta(days=1)
    store.remember("place", "home city", "Toronto")
    assert store.forget("home city") == 2
    assert store.current() == [] and store.history("home city") == []
    assert store.forget("nothing like this") == 0


def test_relevant_and_render_block_bounded(store):
    store.remember("preference", "email style", "short, no greetings, sign as Eric")
    store.remember("preference", "diet", "vegetarian")
    store.remember("person", "mom", "Linda Song", email="linda.song@gmail.com")
    for i in range(30):
        store.remember("fact", f"misc {i}", f"value {i}")
    block = store.render_block("can you email mom the cabin photos")
    lines = block.splitlines()
    assert len(lines) <= 14
    assert any(line.startswith("- mom (person): Linda Song <linda.song@gmail.com>") for line in lines)
    assert any("vegetarian" in line for line in lines)  # preferences always shown
    assert store.render_block("hi") != "None"


def test_empty_store_renders_none(store):
    assert store.render_block("anything") == "None"


def test_rejects_empty_and_overlong(store):
    assert store.remember("fact", "", "x") == "skip"
    assert store.remember("fact", "x", "") == "skip"
    assert store.remember("fact", "bio", "y" * 2000) == "add"
    assert len(store.current()[0].value) <= 300


def test_kind_is_validated(store):
    assert store.remember("nonsense", "hobby", "climbing") == "add"
    assert store.current()[0].kind == "fact"
