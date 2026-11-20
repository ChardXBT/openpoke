# Build prompt: Reference-First Routing for OpenPoke

You are building the agent-overload fix for OpenPoke in the repo at
`C:\Users\ericy\Downloads\openpoke`, branch `agent-overload`, fork
`github.com/ChardXBT/openpoke` (upstream `shlokkhemani/openpoke` at `5b5f635`).

Read first, in order:
1. `docs/superpowers/specs/2026-11-20-agent-overload-design.md` (the spec; it wins any conflict)
2. `docs/research/2026-11-20-fork-survey.md` (what others built and where it failed)
3. `server/agents/interaction_agent/{agent.py,tools.py,runtime.py,system_prompt.md}`,
   `server/agents/execution_agent/{agent.py,runtime.py,batch_manager.py}`,
   `server/services/execution/{roster.py,log_store.py}`, `server/services/triggers/*`,
   `server/services/gmail/importance_watcher.py`

## The goal in one paragraph

The common fix for agent overload (cards + retrieval + search + duplicate guard) reaches
~0.9 routing accuracy, and its remaining errors are reference errors: which draft "send
it" means, which Alice, old thread or new work. Build a resolver that settles references
in code from data the system already has (thread/draft/contact IDs, `#ids`, open items
just shown to the user, email-event metadata), an ambiguity contract that makes "ask the
user" a code-enforced outcome for irreversible actions, and a bounded open-loop working
set in the prompt. Prove it against today's OpenPoke and against an honest
implementation of the common fix, on the failure classes the forks reported, up to
20,000 agents, with intervals and a held-out set.

## Hard rules

- **Money**: company keys only, from `.env`. The eval runner must stop at
  `OPENPOKE_EVAL_BUDGET_USD` (35) using per-call usage and OpenRouter list prices, and
  must set `max_tokens` on every call. Never switch to a personal key without the owner's
  explicit OK in chat.
- **Secrets**: never print, log or commit keys. `.env` stays git-ignored. Before every
  push, scan the staged diff for keys.
- **Originality**: no code or data from other forks. Reading them for context is fine.
- **Compatibility**: keep upstream behaviour reachable behind `OPENPOKE_ROUTER=legacy`
  (default `reference`). Agent names remain the key for logs and triggers.
- **Tests with every change**: each module lands with its pytest tests; the full suite
  stays green and offline (no network, no keys).
- **Eval honesty**: temperature 0; tune on dev only; hash the held-out set before Ours
  first runs on it, then run it once per setup; report misses as they are.
- **Windows**: everything must run on Windows with Python 3.14 (make `fcntl` optional).
- **Commits**: small, one concern each, message body ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push only when the owner has
  OK'd the repo's visibility.
- **Owner updates**: after each milestone, tell the owner in plain words what changed,
  why, what's next and the ETA. Ask before anything that costs money or is outward-facing.

## Build order and done-when

1. **Setup (0:30)**
   - venv with `server/requirements.txt` + `pytest`; record anything that won't install.
   - Query OpenRouter's model list; confirm the IA model (or pick and record a substitute)
     and a cheap dev model; read their prices.
   - One smoke call through the real IA prompt builder with the real tool schemas.
   - Commit `evals/schema.py` (World, Agent, Ref, ConversationTurn, Scenario, Gold) and
     start the Kiro job below.
   - Done when: smoke call returns a tool call; schema committed; Kiro running.
2. **Registry, loops, working set, search (→ 1:45)** per spec 5.1–5.4.
   - Tool-call hook in the execution runtime records refs and state.
   - Done when: migration from `roster.json` works; working set stays ≤ 31 lines at 20,000
     agents; search finds exact-ref and topic matches with collapse; tests pass.
3. **Resolver, ambiguity contract, spawn check, prompt (→ 2:45)** per spec 5.5–5.7, 5.9.
   - Done when: each resolver rule has passing tests; irreversible + ambiguous is blocked
     with `needs_user_choice`; reversible never blocked; no forced ask on a single resolved
     candidate; thread-owned work cannot spawn a duplicate; scripted fake-LLM test passes
     end to end through the IA tool loop.
4. **Eval runner and baselines (→ 3:45)** per spec section 7.
   - B0 and B1 on the vendored upstream prompt; B2 = common fix, built and tuned on dev
     with the same care; Ours plus ablations.
   - Dev runs on the cheap model at 10/200/1,000; read failures; fix causes, not scenarios.
   - Done when: one command runs a setup × size × split and writes JSONL + a summary with
     bootstrap intervals and paired deltas; the budget guard is tested.
5. **Headline runs (→ 4:30)**
   - Hash the held-out set; run all setups on it at all sizes on the IA model; one second
     model on Ours vs B2 at 1,000.
   - Charts: accuracy vs size, clarify recall vs over-ask, tokens per turn vs size.
6. **Beyond the brief (→ 4:50)** per spec section 6, each with a test.
7. **Write-up and review (→ 5:30)**
   - README section: framing, what changed, how to run, results with intervals, what we
     tried and cut, gaps beyond the brief, next steps, assumptions.
   - Independent review (Kiro or Devin) of the diff and a clean test re-run; fix findings.
   - PR from `agent-overload` into the fork's `main`.

Stretch, only if milestones 1–6 are done: learning from corrections (spec 5.8) with its
repeat-reference scenarios.

## Kiro job: world and scenario generator

Model Opus 5.5, effort high. Writes only under `evals/world/` and `tests/evals/`.
- Input contract: `evals/schema.py` (do not edit it; ask if it blocks you).
- `evals/world/generate.py`: `build_world(size, seed) -> World` with ~40 contacts including
  collision families (two Alices at different companies; one Alice with four live topics;
  two clients at one company), ~30 task archetypes, agents spread over 90 days with names
  in the style the IA writes ("Email to Alice", "Alice – Q3 deck draft", "Tokyo ramen
  search"), states, refs (contact, thread, draft, subject, trigger) and a recent
  conversation tail with agent IDs on agent results. Gold agents and decoys exist at every
  size; filler scales to 20,000 quickly (< 10 s).
- `evals/world/scenarios.py`: the ten families from spec section 7, ~80 dev from seed 1 and
  ~30 held-out from seed 2, each with gold `{agent_id | NEW | ASK}` and a risk flag.
  Families 4 and 5 must be balanced so asking is not free.
- Tests: determinism by seed, sizes, gold agents present, family counts, no dev/held-out
  overlap.
- Done when: `pytest tests/evals` passes offline and `python -m evals.world.generate --size
  20000 --seed 1` prints counts.

## Deliverables checklist

- [ ] Working fix behind `OPENPOKE_ROUTER=reference`, legacy reachable
- [ ] pytest suite, offline, green on Windows
- [ ] Eval harness, results JSONL, summary tables and charts
- [ ] README section with assumptions, how to run, results, tried/cut, beyond-the-brief, next
- [ ] Beyond-the-brief fixes with tests
- [ ] PR into the fork's `main`
