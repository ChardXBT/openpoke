"""Pool the held-out runs into one clean rows file per model, then build reports.

    python -m evals.results.assemble

Rules (also stated in JOURNAL.md):
- Ours uses `heldout-haiku-ours-final` (final code) when present; the earlier Ours rows in
  `heldout-haiku` (pre-guard-fix code) are kept only in that run's own folder.
- `heldout-haiku-scale` contributes its complete 5,000-agent cells only (its 20,000 stage was
  stopped and redone in `heldout-haiku-20k`).
- Every row keeps its run name in `run`, so each cell is traceable to its log.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness.report import charts, summary_markdown

RUNS = Path(__file__).resolve().parents[1] / "runs"
RESULTS = Path(__file__).resolve().parent


def _rows(run: str) -> list[dict]:
    path = RUNS / run / "rows.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["run"] = run
            out.append(row)
    return out


def haiku_rows() -> list[dict]:
    final_ours = _rows("heldout-haiku-ours-final")
    rows = [r for r in _rows("heldout-haiku") if not (final_ours and r["setup"] == "ours")]
    rows += [r for r in _rows("heldout-haiku-scale")
             if r["size"] == 5000 and not (final_ours and r["setup"] == "ours")]
    rows += [r for r in _rows("heldout-haiku-20k") if not (final_ours and r["setup"] == "ours")]
    rows += _rows("heldout-b0-20k") + _rows("heldout-b1") + final_ours
    if not final_ours:
        rows += [r for r in _rows("heldout-haiku-scale") if r["setup"] == "ours" and r["size"] == 5000]
    return rows


def write(name: str, rows: list[dict], chart_model: str | None = None) -> None:
    if not rows:
        return
    out = RESULTS / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "rows.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(summary_markdown(rows), encoding="utf-8")
    charts(rows, out, chart_model)
    print(f"{name}: {len(rows)} rows -> {out}")


def main() -> None:
    write("heldout-haiku", haiku_rows(), "anthropic/claude-haiku-5.5")
    write("heldout-sonnet", _rows("heldout-sonnet") + _rows("heldout-sonnet-5k"), "anthropic/claude-sonnet-5.5")
    write("heldout-gpt", _rows("heldout-gpt"), "openai:gpt-6.1-terra")
    write("heldout-ablation", _rows("heldout-ablation") + [r for r in _rows("heldout-haiku-ours-final") if r["size"] == 1000])
    external = _rows("external-haiku")
    for row in external:  # one pooled cell per source: their roster sizes differ per case
        row["size"] = {"kareemelmaaddawy": 200, "draggle": 50}.get(row.get("source"), row["size"])
    write("external-haiku", external)


if __name__ == "__main__":
    main()
