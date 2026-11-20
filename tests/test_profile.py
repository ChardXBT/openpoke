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


def test_relationship_spellings_share_one_subject(store):
    assert store.remember("person", "my mother", "Linda") == "add"
    assert store.remember("person", "Mom", "Linda Song", email="linda@x.com") == "update"
    assert store.person("mum").value == "Linda Song"
    assert [f.subject for f in store.current()] == ["mom"]


def test_relevant_by_name_related_word_and_email(store):
    store.remember("person", "sister", "Sarah Kim", email="sarah.kim@x.com")
    store.remember("person", "partner", "Jordan Lee")
    store.remember("person", "boss", "Dr. Priya Shah")
    for i in range(20):
        store.remember("fact", f"misc {i}", f"value {i}")

    def subjects(text):
        return [f.subject for f in store.relevant(text)]

    assert subjects("email Sarah about the cabin")[0] == "sister"
    assert subjects("ask my wife if Saturday works")[0] == "partner"
    assert subjects("tell my manager I'm out")[0] == "boss"
    assert subjects("what did my mother say") == []  # no mom on file: nothing, not a guess
    assert subjects("forward it to sarah.kim@x.com")[0] == "sister"
    assert "dr" not in {w for f in store.relevant("dr appointment") for w in f.subject.split()}


def test_temporary_fact_shows_until_then_drops_out(store, clock):
    assert store.remember("place", "current location", "Lisbon", until="2026-11-22") == "add"
    assert store.current()[0].line() == "- current location (place): Lisbon (until 2026-11-22)"
    assert store.remember("place", "current location", "Lisbon", until="2026-11-22") == "noop"
    assert store.remember("place", "trip", "Paris", until="2026-11-01") == "skip"  # already over
    assert store.remember("place", "trip", "Paris", until="next friday") == "skip"  # unreadable
    clock.now += timedelta(days=3)
    assert store.current() == []
    assert [f.value for f in store.history("current location")] == ["Lisbon"]
    assert store.remember("place", "current location", "Porto") == "add"  # expired: a fresh fact


def test_retire_ends_a_fact_but_keeps_history(store, clock):
    store.remember("fact", "job", "engineer at Shopify")
    clock.now += timedelta(days=1)
    assert store.retire("my job") == 1
    assert store.current() == [] and len(store.history("job")) == 1
    assert store.retire("job") == 0


def test_small_profile_is_shown_whole_and_temporary_facts_always(store):
    store.remember("place", "home city", "Toronto")
    store.remember("fact", "school", "University of Toronto")
    block = store.render_block("hi")
    assert "Toronto" in block and "University of Toronto" in block
    for i in range(20):
        store.remember("fact", f"misc {i}", f"value {i}")
    store.remember("place", "current location", "Lisbon", until="2026-11-25")
    assert "Lisbon (until 2026-11-25)" in store.render_block("hi")
