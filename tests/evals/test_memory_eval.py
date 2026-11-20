"""The memory eval's grader, run offline with hand-written model replies."""

import asyncio
import json

from evals.harness.llm import LLMResult
from evals.memory import run as mem
from evals.memory.cases import CASES

BY_ID = {c["id"]: c for c in CASES}


class FakeClient:
    model = "fake"

    def __init__(self, replies):
        self.replies = replies
        self.seen = []

    async def complete(self, system, messages, tools):
        self.seen.append(messages[0]["content"])
        return LLMResult({"content": self.replies.pop(0)}, 10, 10, 0.0, 0.01)


def _run(case_id, reply, variant="v2"):
    client = FakeClient([json.dumps(reply)])
    row = asyncio.run(mem.run_case(client, variant, BY_ID[case_id], 0))
    return row, client.seen[0]


def test_case_ids_unique_and_groups_known():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) and len(ids) >= 50
    assert {c["group"] for c in CASES} == {"people", "preferences", "places", "updates", "retire", "temporary",
                                           "context", "negative", "injection"}


def test_update_must_reuse_the_subject():
    good, prompt = _run("u1", {"facts": [{"kind": "place", "subject": "home city", "value": "Toronto"}]})
    assert good["passed"] and "- home city (place): Vancouver" in prompt
    bad, _ = _run("u1", {"facts": [{"kind": "place", "subject": "city", "value": "Toronto"}]})
    assert not bad["passed"] and any("vancouver" in r for r in bad["reasons"])


def test_retire_until_and_nothing_cases():
    assert _run("r1", {"facts": [], "retire": ["car"]})[0]["passed"]
    assert not _run("r1", {"facts": []})[0]["passed"]
    assert _run("t1", {"facts": [{"kind": "place", "subject": "current location", "value": "Lisbon",
                                  "until": "2026-11-22"}]})[0]["passed"]
    assert not _run("t1", {"facts": [{"kind": "place", "subject": "current location", "value": "Lisbon"}]})[0]["passed"]
    assert _run("c5", {"facts": []})[0]["passed"]
    injected, _ = _run("c5", {"facts": [{"kind": "person", "subject": "accountant", "value": "Bob Stone 416-555-0199"}]})
    assert not injected["passed"] and any("bob" in r for r in injected["reasons"])


def test_v1_sees_only_the_message_and_summary_counts():
    row, prompt = _run("c1", {"facts": []}, variant="v1")
    assert prompt == "User message:\nshe's my sister" and not row["passed"]
    summary = mem.summarize([row, {**row, "passed": True, "group": "negative", "case": "n1"}])
    assert summary["fake v1"]["n"] == 2 and summary["fake v1"]["false_write_rate"] == 0.0
    assert "| fake v1 |" in mem.report(summary)


def test_heldout_cases_are_frozen_and_distinct():
    from evals.memory.cases_heldout import HELDOUT_CASES

    assert mem.heldout_hash_ok(HELDOUT_CASES)
    assert not mem.heldout_hash_ok(HELDOUT_CASES[:-1])
    dev_messages = {c["message"] for c in CASES}
    assert not dev_messages & {c["message"] for c in HELDOUT_CASES}
    assert {c["id"] for c in CASES}.isdisjoint({c["id"] for c in HELDOUT_CASES})
