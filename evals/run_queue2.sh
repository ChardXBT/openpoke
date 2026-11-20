#!/bin/bash
# Second queue: waits for the first queue to finish, then runs one job at a time.
cd /c/Users/ericy/Downloads/openpoke
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
PY=.venv/Scripts/python
until grep -q "queue done" evals/runs/queue.log 2>/dev/null; do sleep 20; done
run() { local name=$1; shift; echo "=== $(date '+%H:%M:%S') start $name"; "$@" > evals/runs/$name.log 2>&1; echo "=== $(date '+%H:%M:%S') end $name exit=$?"; }
run heldout-haiku-ours-final $PY -m evals.harness.runner --setups ours --split heldout --sizes 30,200,1000,5000,20000 --reps 2 --model anthropic/claude-haiku-5.5 --concurrency 4 --max-usd 1.5 --out evals/runs/heldout-haiku-ours-final
run external-haiku $PY -m evals.external.run_external --setups b0,b2,ours --model anthropic/claude-haiku-5.5 --concurrency 8 --max-usd 2 --out evals/runs/external-haiku
run heldout-sonnet-5k $PY -m evals.harness.runner --setups b2,ours --split heldout --sizes 5000 --reps 1 --model anthropic/claude-sonnet-5.5 --concurrency 6 --max-usd 3.5 --out evals/runs/heldout-sonnet-5k
echo "=== queue2 done"
