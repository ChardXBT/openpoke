#!/bin/bash
# Headline queue: runs one after another (memory-friendly). Each run has its own spend cap.
cd /c/Users/ericy/Downloads/openpoke
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
PY=.venv/Scripts/python
run() { local name=$1; shift; echo "=== $(date '+%H:%M:%S') start $name"; $PY -m evals.harness.runner "$@" --out evals/runs/$name > evals/runs/$name.log 2>&1; echo "=== $(date '+%H:%M:%S') end $name exit=$?"; }
run heldout-haiku-20k --setups b2,ours --split heldout --sizes 20000 --reps 2 --model anthropic/claude-haiku-5.5 --concurrency 4 --max-usd 2
run heldout-sonnet   --setups b0,b2,ours --split heldout --sizes 30,1000,5000 --reps 1 --model anthropic/claude-sonnet-5.5 --concurrency 10 --max-usd 20
run heldout-b0-20k   --setups b0 --split heldout --sizes 20000 --limit 12 --reps 1 --model anthropic/claude-haiku-5.5 --concurrency 6 --max-usd 4
run heldout-b1       --setups b1 --split heldout --sizes 30,200,1000 --reps 1 --model anthropic/claude-haiku-5.5 --concurrency 10 --max-usd 2
run heldout-ablation --setups ours-no_resolver,ours-no_contract,ours-no_related --split heldout --sizes 1000 --reps 2 --model anthropic/claude-haiku-5.5 --concurrency 10 --max-usd 1
run heldout-gpt      --setups b0,b2,ours --split heldout --sizes 30,1000 --reps 1 --model openai:gpt-6.1-terra --concurrency 8 --max-usd 10
echo "=== queue done"
