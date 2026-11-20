import json

from evals.harness.report import charts, load_rows, summary_markdown


def _row(setup, size, scenario, outcome, gold="agent", family=1, tokens=1000):
    return {"setup": setup, "model": "m", "split": "dev", "size": size, "scenario_id": scenario, "family": family,
            "rep": 0, "gold_kind": gold, "outcome": outcome, "prompt_tokens": tokens, "cost_usd": 0.001,
            "latency_s": 1.0}


def _rows():
    rows = []
    for size in (30, 1000):
        for i in range(6):
            rows.append(_row("ours", size, f"s{i}", "correct", tokens=1500))
            rows.append(_row("b0_names", size, f"s{i}", "correct" if i < 3 else "duplicate", tokens=size * 10))
        rows.append(_row("ours", size, "ask", "correct", gold="ASK", family=4))
        rows.append(_row("b0_names", size, "ask", "missed_ask", gold="ASK", family=4))
    return rows


def test_report_from_fixture_rows(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in _rows()), encoding="utf-8")
    rows = load_rows([run])
    md = summary_markdown(rows)
    assert "| 1,000 | Ours (reference-first) | 7 | 100%" in md
    assert "OpenPoke today" in md and "Paired difference" in md and "Truly ambiguous" in md
    paths = charts(rows, tmp_path / "out")
    assert {p.name for p in paths} == {"accuracy_vs_size.png", "tokens_vs_size.png", "asking.png"}
    assert all(p.stat().st_size > 5000 for p in paths)
