#!/usr/bin/env bash
# heldout2: the fresh split frozen in 29d59d2 (scenarios + routing-code digest), run once per cell.
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python; H=anthropic/claude-haiku-5.5; G=openai:gpt-6.1-terra
export PYTHONIOENCODING=utf-8
run() { local name=$1; shift; echo "=== $(date '+%H:%M:%S') start $name ($(git rev-parse --short HEAD))"; "$@" > evals/runs/$name.log 2>&1; echo "=== $(date '+%H:%M:%S') end $name exit=$?"; }
run heldout2-haiku-small $PY -m evals.harness.runner --setups b0,b2,ours --split heldout2 --sizes 30,1000 --model $H --concurrency 8 --max-usd 0.5 --out evals/runs/heldout2-haiku-small
run heldout2-haiku-large $PY -m evals.harness.runner --setups b2,ours --split heldout2 --sizes 5000,20000 --model $H --concurrency 8 --max-usd 0.3 --out evals/runs/heldout2-haiku-large
run heldout2-gpt-small   $PY -m evals.harness.runner --setups b0,b2,ours --split heldout2 --sizes 30,1000 --model $G --concurrency 8 --max-usd 3.4 --out evals/runs/heldout2-gpt-small
run heldout2-gpt-large   $PY -m evals.harness.runner --setups b2,ours --split heldout2 --sizes 5000,20000 --model $G --concurrency 6 --max-usd 1.8 --out evals/runs/heldout2-gpt-large
echo "=== heldout2 done"
