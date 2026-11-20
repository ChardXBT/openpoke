"""Compute first-call prompt sizes for every setup and size (offline). Writes evals/results/prompt_sizes.json.

    python -m evals.harness.prompt_sizes
"""
import os, sys, json, statistics
sys.path.insert(0, os.getcwd())
from pathlib import Path
from evals.world.generate import build_world
from evals.world.scenarios import build_scenarios
from evals.harness.runner import make_setup
out = {}
for size in (30, 200, 1000, 5000, 20000):
    w = build_world(size, 2); sc = build_scenarios(w)[:6]
    for name in ("b0", "b1", "b2-lexical", "ours"):
        setup = make_setup(name)
        chars = [setup.prepare(w, s, Path(os.environ["TEMP"])).prompt_chars for s in sc]
        out[f"{name}|{size}"] = round(statistics.fmean(chars) / 3.5)
        print(f"{name:<11} {size:>6}  ~{out[f'{name}|{size}']:>8,} tokens per call (first call, estimated)", flush=True)
Path("evals/results/prompt_sizes.json").write_text(json.dumps(out, indent=2))
