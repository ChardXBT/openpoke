#!/bin/bash
# Final queue on fully merged code (independent review fixes in). One job at a time, each capped.
# OpenRouter (Haiku) first to use up its last ~$2, then OpenAI (GPT-6.1), keeping ~$3 of OpenAI for the demo.
cd /c/Users/ericy/Downloads/openpoke
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 OPENPOKE_EVAL_BUDGET_USD=49.6
PY=.venv/Scripts/python
H=anthropic/claude-haiku-5.5; G=openai:gpt-6.1-terra
run() { local name=$1; shift; echo "=== $(date '+%H:%M:%S') start $name ($(git rev-parse --short HEAD))"; "$@" > evals/runs/$name.log 2>&1; echo "=== $(date '+%H:%M:%S') end $name exit=$?"; }
run external-haiku-final $PY -m evals.external.run_external --setups b0,b2,ours --model $H --concurrency 8 --max-usd 1.3 --out evals/runs/external-haiku-final
run dev-haiku-final      $PY -m evals.harness.runner --setups b2,ours --split dev --sizes 1000 --model $H --concurrency 8 --max-usd 0.4 --out evals/runs/dev-haiku-final
run heldout-haiku-post   $PY -m evals.harness.runner --setups b2,ours --split heldout --sizes 1000 --model $H --concurrency 8 --max-usd 0.4 --out evals/runs/heldout-haiku-post
run external-gpt-final   $PY -m evals.external.run_external --setups b0,b2,ours --model $G --concurrency 8 --max-usd 8 --out evals/runs/external-gpt-final
run heldout-gpt-post     $PY -m evals.harness.runner --setups b0,b2,ours --split heldout --sizes 30,1000 --model $G --concurrency 8 --max-usd 6 --out evals/runs/heldout-gpt-post
run heldout-gpt-post-5k  $PY -m evals.harness.runner --setups b2,ours --split heldout --sizes 5000 --model $G --concurrency 6 --max-usd 2.5 --out evals/runs/heldout-gpt-post-5k
run heldout-gpt-b0-20k   $PY -m evals.harness.runner --setups b0 --split heldout --sizes 20000 --sample 12 --model $G --concurrency 4 --max-usd 5 --out evals/runs/heldout-gpt-b0-20k
run dev-gpt-final        $PY -m evals.harness.runner --setups b0,b2,ours --split dev --sizes 30,1000 --model $G --concurrency 8 --max-usd 4 --out evals/runs/dev-gpt-final
echo "=== queue4 done"
