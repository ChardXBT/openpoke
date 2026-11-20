"""Turn run rows into summary tables (Markdown) and three charts (PNG).

    python -m evals.harness.report evals/runs/<run> [more run dirs] --out evals/results/<name>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable, Optional

from evals.harness.grade import paired_delta, summarize

# Fixed colour per setup (validated categorical slots 1-4, then 5-7 for ablations).
# Colour follows the setup, never its rank. Labels are drawn on the chart (contrast relief).
COLORS = {
    "ours": "#2a78d6",
    "b2_common": "#eb6834",
    "b2_common-lexical": "#eb6834",
    "b1_cards": "#1baf7a",
    "b0_names": "#eda100",
    "ours-no_contract": "#e87ba4",
    "ours-no_resolver": "#008300",
    "ours-no_related": "#6250d6",
}
LABELS = {
    "ours": "Ours (reference-first)",
    "b2_common": "Common fork answer",
    "b2_common-lexical": "Common fork answer (lexical only)",
    "b1_cards": "Full list + descriptions",
    "b0_names": "OpenPoke today",
    "ours-no_contract": "Ours without ask-guard",
    "ours-no_resolver": "Ours without resolver",
    "ours-no_related": "Ours without related",
}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a64", "#e6e5e0", "#fcfcfb"


def load_rows(run_dirs: Iterable[Path]) -> list[dict]:
    rows: list[dict] = []
    for run in run_dirs:
        path = Path(run) / "rows.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _pct(x: float) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.0f}%"


def _ci(ci) -> str:
    lo, hi = ci
    if any(isinstance(v, float) and math.isnan(v) for v in (lo, hi)):
        return ""
    return f" [{100 * lo:.0f}–{100 * hi:.0f}]"


def summary_markdown(rows: list[dict]) -> str:
    summary = summarize(rows)
    lines = ["# Agent-overload eval results", ""]
    models = sorted({r["model"] for r in rows})
    for model in models:
        lines += [f"## Model: `{model}`", "",
                  "| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |",
                  "|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        keys = [k for k in summary if k.split("|")[1] == model]
        for key in sorted(keys, key=lambda k: (int(k.split("|")[2]), k.split("|")[0])):
            setup, _, size = key.split("|")
            s = summary[key]
            fit = f" (+{s['does_not_fit']} did not fit)" if s["does_not_fit"] else ""
            lines.append(
                f"| {int(size):,} | {LABELS.get(setup, setup)} | {s['n']}{fit} | {_pct(s['accuracy'])}{_ci(s['accuracy_ci'])} | "
                f"{_pct(s['duplicate_rate'])} | {_pct(s['wrong_reuse_rate'])} | {_pct(s['false_reuse_rate'])} | "
                f"{_pct(s['clarify_recall'])} | {_pct(s['over_ask_rate'])} | {s['prompt_tokens_mean']:,.0f} | "
                f"{s['latency_p50']:.1f} | {s['cost_per_turn']:.4f} |"
            )
        lines.append("")
        lines += _paired_section(rows, model)
        lines += _family_section(summary, model)
    return "\n".join(lines) + "\n"


def _paired_section(rows: list[dict], model: str) -> list[str]:
    out = ["### Paired difference in correct rate (Ours minus baseline, same scenarios)", "",
           "| Agents | Baseline | Δ correct [95% CI] | pairs |", "|---:|---|---|---:|"]
    by = {}
    for r in rows:
        if r["model"] == model:
            by.setdefault((r["setup"], r["size"]), []).append(r)
    sizes = sorted({s for (_, s) in by})
    found = False
    for size in sizes:
        ours = by.get(("ours", size))
        if not ours:
            continue
        for base in ("b2_common", "b2_common-lexical", "b1_cards", "b0_names"):
            if (base, size) in by:
                d, lo, hi, n = paired_delta(ours, by[(base, size)])
                if n:
                    found = True
                    out.append(f"| {size:,} | {LABELS[base]} | {100 * d:+.0f} pts [{100 * lo:+.0f}, {100 * hi:+.0f}] | {n} |")
    return (out + [""]) if found else []


FAMILY_NAMES = {1: "Needle", 2: "Same person, other topic", 3: "\"send it\" / \"did she reply\"", 4: "Truly ambiguous",
                5: "Looks ambiguous, is clear", 6: "New task, look-alikes", 7: "Cross-client", 8: "Inbound email",
                9: "Archived", 10: "Repeat after clarifying"}


def _family_section(summary: dict, model: str) -> list[str]:
    keys = [k for k in summary if k.split("|")[1] == model]
    if not keys:
        return []
    families = sorted({f for k in keys for f in summary[k]["by_family"]}, key=int)
    head = "| Setup | Agents | " + " | ".join(FAMILY_NAMES.get(int(f), str(f)) for f in families) + " |"
    out = ["### Correct rate by scenario family", "", head, "|---|---:|" + "---:|" * len(families)]
    for key in sorted(keys, key=lambda k: (k.split("|")[0], int(k.split("|")[2]))):
        setup, _, size = key.split("|")
        fam = summary[key]["by_family"]
        out.append(f"| {LABELS.get(setup, setup)} | {int(size):,} | " +
                   " | ".join(_pct(fam.get(f, float("nan"))) for f in families) + " |")
    return out + [""]


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def charts(rows: list[dict], out: Path, model: Optional[str] = None) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = summarize(rows)
    model = model or sorted({r["model"] for r in rows})[0]
    series: dict[str, list[tuple[int, dict]]] = {}
    for key, s in summary.items():
        setup, m, size = key.split("|")
        if m == model and s["n"]:
            series.setdefault(setup, []).append((int(size), s))
    for v in series.values():
        v.sort()
    paths: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)

    def line_chart(metric: str, title: str, ylabel: str, fname: str, percent: bool, logy: bool = False) -> None:
        fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=160)
        fig.patch.set_facecolor(SURFACE)
        _style(ax)
        for setup, pts in series.items():
            xs = [p[0] for p in pts]
            ys = [p[1][metric] for p in pts]
            color = COLORS.get(setup, MUTED)
            if metric == "accuracy":
                lo = [p[1]["accuracy_ci"][0] for p in pts]
                hi = [p[1]["accuracy_ci"][1] for p in pts]
                ax.fill_between(xs, lo, hi, color=color, alpha=0.12, linewidth=0)
            ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=6,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=LABELS.get(setup, setup))
            ax.annotate(LABELS.get(setup, setup), (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                        va="center", fontsize=8.5, color=INK)
        ax.set_xscale("log")
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel("Execution agents in the roster (log scale)", color=MUTED, fontsize=9)
        ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
        if percent:
            ax.set_ylim(0, 1.02)
            ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold")
        ax.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=INK)
        fig.tight_layout()
        path = out / fname
        fig.savefig(path, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)

    line_chart("accuracy", f"Right decision rate as agents pile up ({model})", "Correct decisions", "accuracy_vs_size.png", True)
    line_chart("prompt_tokens_mean", "Prompt tokens per turn", "Tokens per turn (all model calls)", "tokens_vs_size.png", False, logy=True)

    # Asking: one dot per setup, pooled over sizes. Top-left is best.
    fig, ax = plt.subplots(figsize=(6.4, 4.4), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    points: dict[tuple[float, float], list[str]] = {}
    for setup, pts in series.items():
        sub = [r for r in rows if r["setup"] == setup and r["model"] == model and r["outcome"] not in ("error", "does_not_fit")]
        ask = [r for r in sub if r["gold_kind"] == "ASK"]
        non = [r for r in sub if r["gold_kind"] != "ASK"]
        if not ask or not non:
            continue
        recall = sum(r["outcome"] == "correct" for r in ask) / len(ask)
        over = sum(r["outcome"] == "over_ask" for r in non) / len(non)
        points.setdefault((round(over, 3), round(recall, 3)), []).append(setup)
        ax.scatter([over], [recall], s=90, color=COLORS.get(setup, MUTED), edgecolors=SURFACE, linewidths=2, zorder=3)
    xmax = max([0.5] + [x + 0.05 for x, _ in points])
    placed_y: list[float] = []
    for (over, recall), setups in sorted(points.items()):
        label = " / ".join(LABELS.get(s, s) for s in setups)  # coincident setups share one label
        right_side = over > 0.6 * xmax
        stacked = sum(1 for y in placed_y if abs(y - recall) < 0.08)  # stagger labels on the same row
        placed_y.append(recall)
        dy = 6 + 14 * stacked if recall < 0.9 else -14 - 14 * stacked
        ax.annotate(label, (over, recall), xytext=(-8 if right_side else 8, dy), textcoords="offset points",
                    ha="right" if right_side else "left", fontsize=8.5, color=INK)
    ax.set_xlim(-0.02, xmax)
    ax.set_ylim(-0.02, 1.05)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlabel("Asked when the answer was clear (over-asking)", color=MUTED, fontsize=9)
    ax.set_ylabel("Asked when it was truly ambiguous", color=MUTED, fontsize=9)
    ax.set_title("Asking the user: top-left is best", loc="left", color=INK, fontsize=11, fontweight="bold")
    fig.tight_layout()
    path = out / "asking.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    paths.append(path)
    return paths


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default=None, help="model for the charts (default: first)")
    args = parser.parse_args(argv)
    rows = load_rows(Path(r) for r in args.runs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary_markdown(rows), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summarize(rows), indent=2, default=str), encoding="utf-8")
    for path in charts(rows, out, args.model):
        print(path)
    print(out / "summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
