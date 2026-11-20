# Agent Overload: Reference-First Routing — Design (v2)

Date: 2026-11-20 · Branch: `agent-overload` · Status: draft for approval
v2 folds in the survey of public forks: `docs/research/2026-11-20-fork-survey.md`.

## 1. Intent

OpenPoke's Interaction Agent (IA) sees every Execution Agent ever created, as a bare
name, on every turn. Over weeks that list reaches hundreds or thousands of agents.

**What others already did.** At least 20 public forks solve this the same way: give the
model richer agent cards, retrieve the top few, add a search tool and a duplicate guard.
They reach roughly 0.86–0.93 routing accuracy at 150–1,000 agents. Their own reports
show where that stops: the model asks the user on only 2 of 30 ambiguous cases, reuses
agents across people and topics, trades wrong reuse for duplicates, and still loses
accuracy as the roster grows (92% at 50 agents → 74% at 1,000).

**Framing.** Those residual errors are reference errors: which draft does "send it"
mean, which Alice, is this the old thread or new work. A model guessing from cards is
the wrong tool for them. Most references can be settled from data the system already
has (thread, draft and contact IDs, `#ids`, the open items it just showed the user).
What cannot be settled is genuinely ambiguous, and the right move is to ask, not guess.

**Thesis.** Resolve references in code where the data allows; make "ambiguous, ask the
user" an outcome enforced in code; let the model choose only among what code could not
settle. Keep the prompt bounded by showing open items, not history.

**Why the prompt stays bounded.** Open items ≈ new tasks per day × days until done
(Little's Law). 30 tasks a day closing in ~2 days ≈ 60 open items, at 500 agents or
200,000. The working set scales with how busy the user is, not with how long they have
used the product.

**User-visible failures we fix**, all at one decision point (`send_message_to_agent`):
- Duplicate: a follow-up spawns a fresh agent that has forgotten the thread.
- Misroute: a follow-up lands on the wrong agent (worst case: sends the wrong draft).
- Silent guess: two things match and the assistant picks one without asking.
- Bloat: the roster grows without bound: ~10 tokens per bare name, ~35 per name with a
  description, resent on every loop iteration. Current 1M-token models still fit 20,000
  names (~200k tokens), so the cost is money, latency and accuracy rather than a hard
  overflow (correction made during the build; an earlier draft assumed a 200k window).

## 2. Goals, non-goals, anti-goals

Goals
- Bounded agent context for the IA at any roster size (10 → 20,000).
- Follow-ups reach the right agent; new work still gets a new agent.
- Ambiguous references produce a short question, enforced in code, not left to the prompt.
- No duplicate agents for thread-bound work (same email thread or draft), by construction.
- Evidence against today's OpenPoke **and** against the common fork answer, on the
  failure classes the forks reported, with intervals and a held-out set.

Non-goals (README next steps)
- Multi-user / 10k-tenant platform work. OpenPoke is single-user by design.
- Running the full app with Gmail/Composio or the web UI.
- Poke's personality or texting voice.
- Re-doing what every fork has (send gate, injection suite, lifecycle simulation);
  we reference them as known and spend the day on the open problem instead.

Anti-goals
- Silent auto-merging or silent redirection of agents.
- Untrusted email text injected into the IA prompt on every turn.
- Asking so often that the assistant becomes annoying (over-ask is a tracked metric).
- Gains that only appear on scenarios built to favour our approach.

## 3. Constraints and assumptions

- Time: one working day, ~5–6 hours to build, evaluate and write up.
- Budget: company-provided OpenRouter + OpenAI keys, ~$50 total, hard limit. The eval
  runner enforces `OPENPOKE_EVAL_BUDGET_USD` (default 35) and always sets `max_tokens`
  (unset, OpenRouter reserves the model's full output allowance per call). No personal
  keys without the owner's explicit OK.
- The brief allows vendoring: the eval imports the fork's real prompt builder and tool
  schemas; only the LLM call is live; Gmail tools are stubbed.
- OpenPoke's configured model (`anthropic/claude-sonnet-4`) is no longer served on
  OpenRouter (checked 2026-11-20). Default is now `anthropic/claude-sonnet-5.5`
  (`OPENPOKE_MODEL` overrides); dev runs use `anthropic/claude-haiku-5.5`.
- Windows dev machine: `roster.py` imports `fcntl` (Unix-only). We make it portable.
- Scenarios are synthetic. Mitigations: a held-out set from a separate seed, frozen by
  hash before the first run of our system; scenario families fixed before tuning; the
  common-answer baseline is built honestly and tuned on dev like ours.
- Public fork (MIT). The assignment brief is not committed. No code or data from other
  forks is used.

## 4. Approaches considered

| Approach | Verdict | Why |
|---|---|---|
| A. Cards + retrieval + search + duplicate guard (the common fork answer) | Baseline B2, and our cold path | Proven to ~0.9; residual errors are reference errors it cannot fix by guessing better. |
| B. One agent per person/project (merge) | Rejected for today | Hard to undo, triggers need re-keying, topics collide inside one agent. |
| C. Fast-model pre-router | Next step | Paid serial call per turn; still a guess. |
| D. Cluster/folder directory | Rejected | Collisions sit on cluster boundaries; extra round trip. |
| **E. Reference-first routing: resolver + ambiguity contract + open-loop working set** | **Picked** | Settles resolvable references deterministically, asks on true ambiguity, bounded prompt; targets exactly the errors A leaves. |

## 5. Design

### 5.1 Agent registry (`server/services/execution/registry.py`)
SQLite at `server/data/execution_agents/agents.db`, source of truth (migrates
`roster.json` on first load; `roster.json` kept for compatibility).

`agents`: `id INTEGER PK` (shown as `#12`), `name`, `name_key` (casefolded,
punctuation-collapsed; unique), `created_at`, `last_active_at` (UTC ISO), `state`,
`state_note` (one line), `topic` (first IA instruction, trimmed), `request_count`,
`archived`, `has_active_trigger`.

`agent_refs`: `(agent_id, kind, value, created_at)`, kinds `contact` (email address),
`contact_name`, `thread`, `draft`, `trigger`, `subject`, `alias`. Captured when tools run
(a hook in the execution runtime), not parsed from logs, which truncate tool args at 200
chars and results at 500.

`agents_fts`: FTS5 over `name`, `topic`, `state_note`, refs text.

Identity stays the agent name for logs and triggers (backward compatible).
`send_message_to_agent` accepts an exact name, a `#id`, or a name matching an existing
`name_key`.

### 5.2 Open-loop state (`server/services/execution/loops.py`)
States: `running`, `waiting_on_user`, `waiting_on_others`, `scheduled`, `done`, `failed`.
- `gmail_create_draft` ok → `waiting_on_user` ("draft to X re Y awaiting your OK").
- `gmail_execute_draft` / `gmail_reply_to_thread` / `gmail_forward_email` ok →
  `waiting_on_others` ("sent to X re Y; waiting for reply").
- `createTrigger` active → `scheduled`; paused/completed triggers recompute.
- Failed run → `failed` (shown until surfaced once). Otherwise → `done`.
- `waiting_on_others` lapses to `done` after 7 days without activity.
- Archive: `done`/`failed`, idle ≥ 14 days, no active trigger. A cheap SQL update at
  render time. Routing to an archived agent reactivates it.

Notes are built only from IA-authored instructions and structured tool fields, never
from email bodies.

### 5.3 Working set in the IA prompt (`agent.py::_render_active_agents`)
`<agents>` replaces `<active_agents>`: open items (non-`done`), most recent first, capped
at 25; up to 5 most recently active others; a count line ("+19,940 more agents not
shown; use search_agents"). Line format (~25–35 tokens):
`#412 Email to Alice – Q3 deck · waiting on you: draft to alice@acme.com re "Q3 deck v2" · 2h ago`

### 5.4 `search_agents` tool
`search_agents(query, include_archived=false, limit=8)`. FTS5 BM25, boosted by exact ref
match, state (open > done > archived) and recency (30-day half-life). Max 2 results per
contact with a "+N more" hint. Optional embeddings fused by RRF behind a flag; default
set by ablation.

### 5.5 Reference resolver (`server/services/execution/resolver.py`) — the core
Runs in code before each IA turn on the incoming user message or event. Output:
`Resolution{kind: resolved|ambiguous|unresolved, candidates: [agent_id…], reason}`.

Order (first rule that fires wins):
1. **Explicit**: `#id`; exact or normalized agent name; an email address, thread ID or
   draft ID present in the message or in the event metadata.
2. **Inbound events**: an email-watcher notification carries `thread_id` and sender;
   it resolves to the agent holding that thread, else the open item waiting on that
   sender. (Fixes agents spawned from alerts alone.)
3. **Conversational reference**: short anaphoric or confirming messages ("send it",
   "yes go ahead", "did she reply?", "cancel that") resolve against the open items the
   assistant surfaced in the last few turns (agent results in the conversation log
   carry agent IDs). One matching open item of the needed kind → resolved; two or more
   → ambiguous.
4. **Entity**: contact names/emails in the message → that contact's agents, open first.
   One live matter → resolved; several live matters → candidates with their notes.
5. Otherwise unresolved: the model decides from the working set, using search if needed.

Rendered into the turn as `<routing_hint>`: "Likely #412 (reason: confirms the draft
shown 2 turns ago)" or "Ambiguous between #412 and #40; ask which before acting".
Learned aliases (5.8) feed rule 3/4.

### 5.6 Ambiguity contract (enforced in `tools.py::send_message_to_agent`)
- If the resolver said `ambiguous` and the instruction is irreversible (send, forward,
  reply, execute, delete, cancel) → do not dispatch; return `needs_user_choice` with the
  options as one-liners. The prompt tells the IA to ask in one short message.
- If the target of an irreversible instruction is not `waiting_on_user` while another
  agent is → `confirm_target`.
- Reversible work (search, draft, look up) is never blocked; ambiguity there is a hint.
- Over-ask guard: no forced question when the resolver resolved to one candidate.

### 5.7 Spawn-or-reuse check
For a name that is not an existing agent: if the resolver or search finds an agent that
owns the same thread/draft → reuse is required (return `owned_by` with the ID). If a
strong match on contact + subject with an open item → `possible_duplicate` with up to 3
cards; re-call with the ID or `create_new: true`. Agents created earlier in the same IA
turn are never flagged (intentional parallel splits). New optional schema field:
`create_new: boolean`.

### 5.8 Stretch: learning from corrections
When the user answers a `needs_user_choice` question, store the reference phrase as an
`alias` ref on the chosen agent ("the lunch one" → #40). The next time, rule 3/4 resolves
it without asking. Evaluated with repeat-reference scenarios.

### 5.9 Prompt changes (`system_prompt.md`)
Explain `<agents>`, `<routing_hint>`, `#ids`, states, `search_agents`,
`needs_user_choice`. Replace "Don't worry if the agent name is unrelated…" with: reuse
an agent when it holds the thread, draft or person+topic; otherwise create one named
"Person/Org – Topic". When asking, offer the options in one short line.

## 6. Beyond the brief (code fixes)

1. Agents spawned from email alerts alone: handled by resolver rule 2 (the watcher passes
   `thread_id` and sender with the notification).
2. Concurrent runs on one agent: per-agent `asyncio.Lock` in `ExecutionBatchManager`.
3. Unbounded execution-agent memory: wire the existing `conversation_limit` (default 20).
4. No `max_tokens` on LLM calls: set a sane default in the OpenRouter client.
5. Windows portability: `fcntl` optional with an `msvcrt` fallback.

README only, with the fix sketched: duplicate trigger fires (no claim/lease), one global
result batch, trigger query cannot use its index, polling watcher, single-tenant storage,
prompt-caching layout, and the code-level send gate others have shown.

## 7. Evaluation

Harness under `evals/`, using the fork's real prompt, message builder and tool schemas;
live model via OpenRouter; Gmail stubbed; IA loop up to 4 iterations with stub tool
results (`search_agents`, the resolver and the guards run for real).

**World generator** (seeded): ~40 contacts including collision families (two Alices; one
Alice with four topics; two clients at one company), ~30 task archetypes, agents spread
over 90 days with IA-style names, states and refs. Scenario gold agents and decoys exist
at every size; filler scales the roster.

**Scenario families**, weighted toward the classes the forks reported failing. ~80 dev
and ~30 held-out, each with recent conversation history, a message or event, gold
`{agent | NEW | ASK}` and a risk flag:
1. Needle: follow-up to an older agent by topic.
2. Same person, different topic (collision).
3. Conversational reference: "send it", "did she reply?", after 1–4 intervening turns.
4. True ambiguity: two drafts or two live matters match → gold ASK.
5. Clear case that looks ambiguous at first glance → must NOT ask (over-ask check).
6. Genuinely new task with look-alikes present (false-reuse trap).
7. Cross-client: same company, different client.
8. Inbound email on a waiting thread → owner agent, not a new one.
9. Archived agent resurrection.
10. Repeat reference after a clarification (stretch, for 5.8).

**Setups**
- B0 Today: names-only full roster, upstream prompt and tools.
- B1 Full roster with descriptions, upstream prompt.
- B2 The common fork answer: cards + BM25/embedding top-K + recent cache + search tool +
  duplicate guard, upstream prompt plus card instructions. Built and tuned on dev with the
  same care as ours.
- Ours. Ablations: resolver off; ambiguity contract off; working set without open-loop
  states; embeddings on/off.

B0–B2 use the upstream prompt and schemas vendored from `5b5f635`, so differences come
from routing.

**Decision extraction**: final `send_message_to_agent` target after the loop (existing →
that agent; unknown name → NEW); no dispatch plus a user-facing question → ASK; anything
else → OTHER (wrong).

**Sizes**: 30, 200, 1,000, 5,000, 20,000 (30 is the number of core agents scenarios depend
on). Setups that do not fit the context window are recorded as "does not fit" from
computed token counts. Large B1 cells are subsampled.

**Metrics**: routing accuracy; duplicate rate; wrong-reuse rate (overall and
cross-person/client); clarify recall (asked when gold is ASK) and over-ask rate (asked
when gold is an agent); wrong-action rate on irreversible steps; prompt tokens per turn;
latency p50/p95; cost per turn. 95% bootstrap intervals; paired comparisons
(Ours vs B2, Ours vs B0).

**Discipline**: temperature 0; dev runs on a cheap model; headline runs on the IA's model
(or its recorded substitute) plus one second model; held-out set hashed before Ours first
runs on it and run once; per-call usage logged with prices from OpenRouter's model list;
JSONL results, summary tables and charts in README.

**Optional evidence (owner-run)**: the owner texts real Poke 8 ambiguity scenarios by
hand; outcomes are recorded and reported as a small, clearly labelled spot check.

**Tests (pytest)**: registry (normalize, migrate, ids), state derivation from fixture tool
calls, bounded working set, search ranking and collapse, every resolver rule, ambiguity
contract paths (irreversible blocked, reversible allowed, no over-ask on resolved),
spawn check paths, archive/reactivate, per-agent lock ordering, `conversation_limit`, and
a scripted fake-LLM behavioural test through the IA tool loop.

## 8. Who builds what

- Claude: registry, resolver, contract, prompt, integration, eval runner, baselines, review.
- Kiro (Opus 5.5 high, own files under `evals/world/`): world + scenario generator against
  a frozen schema in `evals/schema.py`, started once the schema is committed.
- Kiro or Devin at the end: independent review and test re-run.
- Owner: keys (done), approvals, any personal-key spend, the optional real-Poke check.

## 9. Milestones (about 5.5 hours)

1. Setup: venv, model availability, one smoke call through the real prompt; commit
   `evals/schema.py`; start Kiro (0:30).
2. Registry, loops, working set, search + tests (→ 1:45).
3. Resolver, ambiguity contract, spawn check, prompt + tests (→ 2:45).
4. Runner + B0/B1/B2; dev runs on a cheap model; fix what they show (→ 3:45).
5. Held-out hash, headline runs, second model, charts (→ 4:30).
6. Beyond-the-brief fixes + tests (→ 4:50).
7. README, independent review, PR into the fork's `main` (→ 5:30).

## 10. Left to the builder

Exact thresholds (tuned on dev only); the anaphora/confirmation phrase lists; chart
styling; scenario wording.
