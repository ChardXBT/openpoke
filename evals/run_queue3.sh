#!/bin/bash
# Queue 3 (OpenAI key, GPT-6.1-terra): v2 code (resolver earlier-choice fix) on the external
# tests, held-out at every size, and dev. One job at a time; each capped.
cd /c/Users/ericy/Downloads/openpoke
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 OPENPOKE_EVAL_BUDGET_USD=46
PY=.venv/Scripts/python
M=openai:gpt-6.1-terra
run() { local name=$1; shift; echo "=== $(date '+%H:%M:%S') start $name"; "$@" > evals/runs/$name.log 2>&1; echo "=== $(date '+%H:%M:%S') end $name exit=$?"; }
run external-gpt-v2   $PY -m evals.external.run_external --setups b0,b2,ours --model $M --concurrency 8 --max-usd 14 --out evals/runs/external-gpt-v2
run heldout-gpt-v2    $PY -m evals.harness.runner --setups b0,b2,ours --split heldout --sizes 30,200,1000,5000 --reps 1 --model $M --concurrency 8 --max-usd 12 --out evals/runs/heldout-gpt-v2
run heldout-gpt-v2-20k $PY -m evals.harness.runner --setups b2,ours --split heldout --sizes 20000 --reps 1 --model $M --concurrency 4 --max-usd 3 --out evals/runs/heldout-gpt-v2-20k
run dev-gpt-v2        $PY -m evals.harness.runner --setups b0,b2,ours --split dev --sizes 30,1000 --reps 1 --model $M --concurrency 8 --max-usd 8 --out evals/runs/dev-gpt-v2
echo "=== queue3 done"
