# Evals

Live evaluation of agent routing in OpenPoke: the real Interaction Agent prompt and tool
schemas, a live model, Gmail stubbed, one isolated in-memory world copy per scenario.

## Layout

| Path | What |
|---|---|
| `schema.py` | Shared types: `World`, `WorldAgent`, `Scenario`, `Gold`, `PreparedTurn`, `Setup` |
| `world/generate.py` | `build_world(size, seed)`: 30 core agents + realistic filler up to 20,000 |
| `world/scenarios.py` | `build_scenarios(world)`: 10 families; seed 1 = dev (78), seed 2 = held-out (31) |
| `setups/` | B0 `b0_names` (OpenPoke today), B1 `b1_cards` (every agent with a card), B2 `b2_common` (the common fork answer), `ours` (+ ablations `ours-no_resolver`, `ours-no_contract`, `ours-no_related`) |
| `setups/upstream/` | Upstream system prompt and tool schemas vendored from `5b5f635` (B0–B2 use these) |
| `harness/llm.py` | OpenRouter / OpenAI-direct client, per-provider spend ledger, hard caps |
| `harness/runner.py` | Runs setups × sizes × scenarios; one JSONL row per decision |
| `harness/grade.py` | Decision extraction, outcomes, bootstrap CIs, paired deltas |
| `harness/report.py` | `summary.md` tables + charts |
| `results/` | Committed results, `JOURNAL.md` (every run and what changed), `heldout.sha256` |

## Run

```bash
pip install -r server/requirements.txt -r evals/requirements.txt pytest
python -m pytest                       # offline, no keys (182 passed; pytest.ini already adds -q,
                                       # so an extra -q hides the pass/fail count line)

# a dev run (needs OPENROUTER_API_KEY in .env)
python -m evals.harness.runner --setups b0,b1,b2,ours --split dev --sizes 30,200,1000 \
    --model anthropic/claude-haiku-5.5 --max-usd 5 --out evals/runs/dev-1
python -m evals.harness.report evals/runs/dev-1 --out evals/runs/dev-1/report
```

Model ids: OpenRouter ids (`anthropic/claude-sonnet-5.5`), or `openai:<model>` for the
OpenAI API directly (uses `OPENAI_API_KEY`; B2 embeddings use it too).

## Rules the harness enforces

- Every call sets `max_tokens` and `temperature=0` (OpenAI: `reasoning_effort="none"`).
- Spend is capped per run (`--max-usd`) and per provider (`OPENPOKE_EVAL_BUDGET_USD`,
  ledgers in `evals/runs/`); a provider's own credit limit stops the run cleanly.
- Held-out runs refuse to start unless the scenarios match `results/heldout.sha256`
  (frozen before any held-out run).
- Prompts whose estimated size exceeds the model's context are recorded as `does_not_fit`,
  not called.

## Outcomes

`correct`, `duplicate` (made a new agent for existing work), `wrong_reuse` (used the wrong
agent, or reused for genuinely new work), `missed_ask` (acted on a truly ambiguous
irreversible request), `over_ask` (asked when the answer was clear), `other`.
Two family-specific rules: for inbound email (family 8) telling the user without creating
anything is correct; for questions about a finished agent's past result (families 1, 9)
answering straight from the agent's card with its recorded facts is correct.
