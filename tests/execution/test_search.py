from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.registry import AgentRecord, AgentRegistry, Ref, normalize_name
from server.services.execution.search import search_agents

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
