"""The frozen quality eval stays frozen and reproduces its committed baseline."""

import json
from pathlib import Path

from evals.frozen.run import ROOT, cases_hash, summarize

BASELINE = ROOT / "baseline" / "summary.json"


def test_frozen_cases_hash_matches():
    expected = (ROOT / "cases.sha256").read_text(encoding="utf-8").split()[0]
    assert cases_hash() == expected


def test_frozen_eval_reproduces_committed_baseline(tmp_path):
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    current = summarize(cases, tmp_path)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert current == baseline
    assert (current["passed"], current["total"], current["pass_rate"]) == (7, 7, 1.0)
