from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.registry import AgentRecord, AgentRegistry, Ref, normalize_name
from server.services.execution.search import _semantic_scores, search_agents

NOW = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(i, name, topic="", note="", state="done", days=1.0, archived=False):
    return AgentRecord(
        id=i, name=name, name_key=normalize_name(name), created_at=_iso(days + 1), last_active_at=_iso(days),
        state=state, state_note=note, topic=topic, request_count=1, archived=archived, has_active_trigger=False,
    )


@pytest.fixture()
def reg(tmp_path) -> AgentRegistry:
    r = AgentRegistry(tmp_path / "agents.db", clock=lambda: NOW)
    records = [
        _rec(1, "Email to Alice", "Draft the Q3 deck email to Alice", 'draft to alice.chen@acme.com re "Q3 deck v2" awaiting your OK', "waiting_on_user", 0.1),
        _rec(2, "Tokyo Restaurant Search", "Find good ramen and sushi spots in Shinjuku", "", "done", 20),
        _rec(3, "Invoice follow-up", "Chase Bob about the unpaid invoice", 'replied to bob@northwind.com re "Invoice #402"; waiting for reply', "waiting_on_others", 2),
        _rec(4, "Visa paperwork", "Collect documents for the Japan visa", "", "done", 60, archived=True),
        _rec(5, "Alicia birthday gift", "Find a gift for Alicia", "", "done", 5),
    ]
    refs = {
        1: [Ref("contact", "alice.chen@acme.com"), Ref("subject", "Q3 deck v2"), Ref("draft", "dr_19c0")],
        3: [Ref("contact", "bob@northwind.com"), Ref("subject", "Invoice #402"), Ref("thread", "thr_77")],
    }
    # Six agents for one contact to exercise collapsing.
    for i in range(10, 16):
        records.append(_rec(i, f"Carol task {i}", f"Carol project update {i}", "", "done", 3 + i / 10))
        refs[i] = [Ref("contact", "carol@globex.com")]
    r.bulk_insert(records, refs)
    return r


def _ids(hits):
    return [h.agent.id for h in hits]


def test_exact_ref_beats_topic(reg):
    assert _ids(search_agents(reg, "alice.chen@acme.com", now=NOW))[0] == 1
    assert _ids(search_agents(reg, "thr_77", now=NOW))[0] == 3


def test_topic_match(reg):
    assert _ids(search_agents(reg, "ramen tokyo", now=NOW))[0] == 2
    assert 3 in _ids(search_agents(reg, "unpaid invoice from bob", now=NOW))[:2]


def test_hash_id_query(reg):
    assert _ids(search_agents(reg, "#2", now=NOW)) == [2]


@pytest.mark.parametrize("query", ['"', "-", "*", "AND", "NOT alice", "", "   ", "(", "alice OR", "NEAR(", "a:b"])
def test_fts_syntax_is_safe(reg, query):
    search_agents(reg, query, now=NOW)  # must not raise


def test_invoice_number_finds_subject(reg):
    assert _ids(search_agents(reg, "invoice #402", now=NOW))[0] == 3


def test_collapse_per_contact(reg):
    hits = search_agents(reg, "carol project update", now=NOW, limit=8)
    carol = [h for h in hits if h.agent.name.startswith("Carol")]
    assert len(carol) == 2
    assert carol[-1].hidden_for_contact == 4


def test_archived_excluded_unless_requested(reg):
    assert 4 not in _ids(search_agents(reg, "japan visa documents", now=NOW))
    assert _ids(search_agents(reg, "japan visa documents", include_archived=True, now=NOW))[0] == 4


def test_limit_respected(reg):
    assert len(search_agents(reg, "carol alice bob tokyo", now=NOW, limit=3)) <= 3


def test_paraphrase_finds_agent_without_shared_words(reg):
    assert _ids(search_agents(reg, "that japan food list", now=NOW))[0] == 2


def test_exact_lexical_match_outranks_paraphrase_recall(reg):
    extra = _rec(20, "Food pantry donation", "Organize the neighborhood food donation list", "", "done", 0.2)
    reg.bulk_insert([extra], {})
    ids = _ids(search_agents(reg, "food list", now=NOW))
    assert ids[0] == 20
    assert 2 in ids


def test_exact_reference_still_outranks_strong_paraphrase_matches(reg):
    ids = _ids(search_agents(reg, "alice.chen@acme.com food japan invoice", now=NOW))
    assert ids[0] == 1


def test_zero_and_negative_limits_return_no_hits(reg):
    assert search_agents(reg, "japan food", now=NOW, limit=0) == []
    assert search_agents(reg, "japan food", now=NOW, limit=-1) == []


def test_optional_embeddings_add_paraphrase_recall_and_cache_vectors(reg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EMBEDDING_CACHE_DIR", str(tmp_path / "vectors"))
    rec = _rec(40, "Subscription Audit", "Audit recurring subscriptions and duplicate payments", "", "done", 1)
    reg.bulk_insert([rec], {})
    calls: list[list[str]] = []

    def fake_embedder(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        vectors = []
        for text in texts:
            folded = text.casefold()
            if "twice paying" in folded or "subscription audit" in folded:
                vectors.append([1.0, 0.0])
            elif "unrelated" in folded:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.1, 0.9])
        return vectors

    query = "which subscriptions did you say i was twice paying for"
    ids = _ids(search_agents(reg, query, now=NOW, embedder=fake_embedder))
    assert ids[0] == 40
    first_call_count = len(calls)
    assert first_call_count > 0 and (tmp_path / "vectors" / "embeddings.sqlite3").is_file()

    ids = _ids(search_agents(reg, query, now=NOW, embedder=fake_embedder))
    assert ids[0] == 40
    assert len(calls) == first_call_count  # second identical search is served from the vector cache


def test_exact_reference_outranks_semantic_embedding_match(reg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EMBEDDING_CACHE_DIR", str(tmp_path / "vectors"))
    rec = _rec(41, "Subscription Audit", "Audit recurring subscriptions and duplicate payments", "", "done", 0.1)
    reg.bulk_insert([rec], {})

    def fake_embedder(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "subscription" in text.casefold() or "audit recurring" in text.casefold() else [0.0, 1.0] for text in texts]

    ids = _ids(search_agents(reg, "alice.chen@acme.com subscription", now=NOW, embedder=fake_embedder))
    assert ids[0] == 1


def test_embedder_failure_falls_back_to_lexical_search(reg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EMBEDDING_CACHE_DIR", str(tmp_path / "vectors"))

    def broken(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("rate limited")

    assert _ids(search_agents(reg, "ramen tokyo", now=NOW, embedder=broken))[0] == 2


def test_semantic_embedding_recall_without_lexical_overlap_across_vector_batches(reg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EMBEDDING_CACHE_DIR", str(tmp_path / "vectors"))
    records = [
        _rec(100 + i, f"Routine record {100 + i}", f"ordinary operations note {100 + i}", "", "done", 4 + i / 100)
        for i in range(520)
    ]
    target = _rec(700, "Dormant reconcile ledger", "Reconcile dormant quarterly ledger variance", "", "done", 0.2)
    reg.bulk_insert([*records, target], {})

    def fake_embedder(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            folded = text.casefold()
            if "unrelated magic query" in folded or "dormant reconcile ledger" in folded:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors

    ids = _ids(search_agents(reg, "unrelated magic query", now=NOW, embedder=fake_embedder))
    assert ids[:1] == [700]


def test_semantic_candidate_heap_keeps_stable_order_for_duplicate_vectors(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EMBEDDING_CACHE_DIR", str(tmp_path / "vectors"))
    reg = AgentRegistry(tmp_path / "tie-agents.db", clock=lambda: NOW)
    records = [
        _rec(i, f"same card {i}", "identical duplicate semantic text", "", "done", 1)
        for i in range(1, 251)
    ]
    reg.bulk_insert(records, {})

    def identical_embedder(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    semantic = _semantic_scores(reg, "identical duplicate semantic text", identical_embedder)
    assert len(semantic) == 200
    assert max(semantic) == 200
    assert min(semantic) == 1
