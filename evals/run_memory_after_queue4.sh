#!/usr/bin/env bash
# Waits for the routing queue to finish (one eval process at a time on a busy laptop), then runs
# the personal-memory learner eval: v1 (old prompt) vs v2 (product learner), 3 reps per case.
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
while ! grep -q "=== queue4 done" evals/runs/queue4.log 2>/dev/null; do sleep 30; done
echo "=== $(date +%H:%M:%S) start memory-1 ($(git rev-parse --short HEAD))"
PYTHONIOENCODING=utf-8 $PY -m evals.memory.run --models openai:gpt-6-luna,openai:gpt-6.1-terra,anthropic/claude-haiku-5.5 \
  --variants v1,v2 --reps 3 --max-usd 1.5 --concurrency 8 --out evals/runs/memory-1
echo "=== $(date +%H:%M:%S) end memory-1 exit=$?"
