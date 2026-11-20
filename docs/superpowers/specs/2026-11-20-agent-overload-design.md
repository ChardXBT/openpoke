# Agent Overload: Open-Loop Routing — Design

Date: 2026-11-20 · Branch: `agent-overload` · Status: draft for approval

## 1. Intent

OpenPoke's Interaction Agent (IA) sees every Execution Agent ever created, as a bare
name, on every turn. Over weeks that list reaches hundreds or thousands of agents.

**Framing.** Overload is not mainly a list-length problem. It is that Poke cannot tell
what is still unfinished, so it either reads everything or guesses. A good human
assistant tracks open items (a draft waiting for approval, a reply being waited on, a
scheduled reminder), not every task ever done. The prompt should hold what is open;
everything else should be one search away.

**Why this bounds the prompt.** Open items ≈ new tasks per day × days until done
(Little's Law). A user who starts 30 tasks a day that close in ~2 days has ~60 open
items whether they have 500 agents or 200,000. The working set scales with how busy the
user is, not with how long they have used the product.

**User-visible failures we are fixing** (all at one decision point, `send_message_to_agent`):
- Duplicate: a follow-up spawns a fresh agent that has forgotten the thread.
- Misroute: a follow-up lands on the wrong agent and acts on the wrong thing
  (worst case: sends the wrong draft).
- Bloat: the roster grows until the call is slow, expensive, then overflows the context
  window (~15–18k agents at today's ~10 tokens per name).

## 2. Goals, non-goals, anti-goals

Goals
- Bounded, informative agent context for the IA at any roster size (10 → 20,000).
- Follow-ups reach the right agent; new work still gets a new agent.
- No wrong action on irreversible steps: when ambiguous, ask.
- Evidence: an evaluator that runs the real IA prompt with a live model and compares
  today's behaviour, the "obvious" fixes, and ours.

Non-goals (stated in README as next steps)
- Multi-user / 10k-tenant platform work. OpenPoke is single-user by design.
- Running the full app with Gmail/Composio or the web UI.
- Poke's personality or texting voice.

Anti-goals (would be a failure even if it technically works)
- Silent auto-merging or silent redirection of agents (hides errors, irreversible).
- Untrusted email text injected into the IA prompt on every turn.
- Improvements that only show up on scenarios we designed to favour our approach.

## 3. Constraints and assumptions

- Time: one working day, ~5–6 hours to build, evaluate and write up.
- Budget: company-provided OpenRouter + OpenAI keys, about $50 total with a hard limit.
  The eval runner enforces `OPENPOKE_EVAL_BUDGET_USD` (default 35). No personal keys
  without the owner's explicit OK.
- The brief allows vendoring: the eval imports the fork's real prompt builder and tool
  schemas; only the LLM call is live; Gmail tools are stubbed.
- Windows dev machine: `roster.py` imports `fcntl` (Unix-only). We make it portable.
- Scenarios are synthetic (no real usage logs available). Mitigation: scenario
  families are fixed before tuning; a held-out set is never used during development.
- Repo is a public fork (MIT). The assignment brief itself is not committed.

## 4. Approaches considered

| Approach | Verdict | Why |
|---|---|---|
| A. Hybrid search + lifecycle (BM25 + embeddings + recency, top-K cards, archive, duplicate check) | Partly used | Good cold recall, but "recent" is a weak proxy for "relevant", and search precision falls as near-duplicates pile up. Kept as the cold path only. |
| B. One agent per person/project (merge) | Rejected for today | Fixes sprawl at the root but merges are hard to undo, triggers need re-keying, and topics collide inside one larger agent. |
| C. Fast-model pre-router | Next step | Strong on nuance, but adds a paid serial call per turn and does nothing for sprawl. |
| D. Cluster/folder directory | Rejected | Collisions sit on cluster boundaries; extra round trip per route; unproven. |
| **E. Open-loop working set + search + spawn-or-reuse check + ask-when-risky** | **Picked** | Bounded by construction, deterministic signals from tool calls, discriminative cards, measurable safety. Uses A's search for cold recall. |

## 5. Design

### 5.1 Agent registry (`server/services/execution/registry.py`)
SQLite at `server/data/execution_agents/agents.db`, replacing `roster.json` as the
source of truth (migrated on first load; `roster.json` kept for compatibility).

`agents`: `id INTEGER PK` (shown as `#12`), `name`, `name_key` (casefolded,
punctuation-collapsed; unique), `created_at`, `last_active_at` (UTC ISO),
`state`, `state_note` (one line), `topic` (first IA instruction, trimmed),
`request_count`, `archived INTEGER`, `has_active_trigger INTEGER`.

`agent_refs`: `(agent_id, kind, value, created_at)` with kinds `contact`
(email address), `thread`, `draft`, `trigger`, `subject`. Captured when tools run,
not parsed from logs (logs truncate args at 200 chars and results at 500).

`agents_fts`: FTS5 over `name`, `topic`, `state_note`, refs text.

Identity stays the agent name for logs and triggers (backward compatible).
`send_message_to_agent` accepts an exact name, a `#id`, or a name that matches an
existing `name_key` (fixes "Email to Alice" vs "email to alice").

### 5.2 Open-loop state (`server/services/execution/loops.py`)
States: `running`, `waiting_on_user`, `waiting_on_others`, `scheduled`, `done`, `failed`.

Derived from tool calls observed in the execution runtime, then from the final reply:
- `gmail_create_draft` ok → `waiting_on_user` ("draft to X re Y awaiting your OK"); ref draft, contact, subject.
- `gmail_execute_draft` / `gmail_reply_to_thread` / `gmail_forward_email` ok → `waiting_on_others` ("sent to X re Y; waiting for reply"); refs thread, contact.
- `createTrigger` active → `scheduled`; paused/completed triggers recompute.
- Execution failed → `failed` (shown until surfaced once).
- Otherwise → `done`.
- `waiting_on_others` lapses to `done` after 7 days without activity.

Archive: `done`/`failed`, idle ≥ 14 days, no active trigger → `archived=1`. Runs as a
cheap SQL update when the roster is rendered (no background job). Any routing to an
archived agent reactivates it.

State notes are built only from IA-authored instructions and structured tool fields
(addresses, subjects, IDs), never from email bodies (prompt-injection guard).

### 5.3 Working set in the IA prompt (`agent.py::_render_active_agents`)
`<agents>` replaces `<active_agents>`:
- Open items (all non-`done` states), most recent first, capped at 25.
- Up to 5 most recently active other agents.
- A count line: "+19,940 more agents not shown. Use search_agents to find older work."

Line format (~25–35 tokens):
`#412 Email to Alice – Q3 deck · waiting on you: draft to alice@acme.com re "Q3 deck v2" · 2h ago`

### 5.4 `search_agents` tool (new IA tool)
`search_agents(query, include_archived=false, limit=8)`. Ranking: FTS5 BM25 over the
fields above, boosted by exact ref match (email address, `#id`, subject), state
(open > done > archived) and recency (30-day half-life). Results are collapsed so one
contact cannot fill the list (max 2 per contact, "+N more for alice@acme.com").
Optional dense embeddings (`text-embedding-3-small`) fused by RRF behind a flag;
on or off by default is decided by the ablation.

### 5.5 Spawn-or-reuse check (`tools.py::send_message_to_agent`)
For a name that is not an existing agent:
1. Search with name + instructions.
2. If a strong match exists (shared contact/thread with an open item, or a lexical
   score above threshold), do not dispatch. Return `possible_duplicate` with up to 3
   cards and the hint: reuse by calling again with the exact name or `#id`, or pass
   `create_new: true` if this is separate work.
3. Agents created earlier in the same IA turn are never flagged (intentional parallel splits).

Irreversible guard: if instructions send/forward/reply/execute and the target agent
is not `waiting_on_user` while other agents are, return `confirm_target` listing the
agents with pending drafts; the IA must ask the user.

Schema change: optional `create_new: boolean` on `send_message_to_agent`.

### 5.6 Prompt changes (`system_prompt.md`)
- Explain the `<agents>` block, `#ids`, states and `search_agents`.
- Replace "Don't worry if the agent name is unrelated…" with: reuse an agent when it
  holds the relevant thread, draft or person+topic; otherwise create one named
  "Person/Org – Topic".
- "If a confirmation could match more than one waiting draft, ask which one."

### 5.7 Stretch: inbound email linking
Before the email watcher dispatches a notification, look up `agent_refs` by thread
and sender; annotate "Related: #77 Invoice #402 follow-up (waiting on reply from
bob@…)". Only if core and eval are done.

## 6. Beyond the brief (code fixes)

1. Concurrent runs on one agent: per-agent `asyncio.Lock` in `ExecutionBatchManager`
   so runs on the same agent are serialized in order.
2. Unbounded execution-agent memory: wire the existing `conversation_limit` through a
   setting (default 20 requests); test it.
3. Windows portability: `fcntl` optional with an `msvcrt` fallback.

README only, with the fix sketched: triggers can fire twice (no claim/lease, next fire
updated after completion), one global result batch (a slow agent holds back unrelated
answers), trigger query cannot use its index, polling email watcher, single-tenant
storage, prompt caching layout.

## 7. Evaluation

Harness under `evals/`. Uses the fork's real system prompt, message builder and tool
schemas; live model via OpenRouter; Gmail tools stubbed; IA loop up to 4 iterations
with stub tool results (`search_agents` and the spawn check run for real).

**World generator** (seeded): ~40 contacts including collision families (two Alices;
one Alice with four topics), ~30 task archetypes, agents spread over 90 days with
realistic IA-style names, states and refs. Scenario gold agents and their decoys exist
at every size; filler agents scale the roster.

**Scenario families** (~60 dev + ~20 held-out), each with recent conversation history,
a message (user or watcher), and gold `{agent | NEW | ASK}` plus a risk flag:
1. Needle: follow-up to an older agent by topic.
2. Collision: same person, different topic.
3. Reference/recency: "did she reply?", "send it", with history ending on an agent result.
4. Genuinely new task with look-alikes present (false-reuse trap).
5. Archived agent resurrection.
6. Irreversible ambiguity: two drafts waiting, vague confirmation → gold ASK.
7. Inbound email from a waiting thread (stretch).

**Setups**
- B0 Today: names-only full roster (unchanged code path).
- B1 Obvious: full roster with descriptions.
- B2 Author's suggestions: embedding top-15 + 8 most recent hot cache.
- Ours: working set + `search_agents` + spawn check + ask-when-risky.
- Ablations of ours: no search tool; no spawn check; with/without embeddings.

B0–B2 use the upstream system prompt and tool schemas, vendored from upstream commit
`5b5f635`, so the comparison isolates the routing change; only the agents block differs.
Ours uses the modified prompt and tools.

**Decision extraction**: the final `send_message_to_agent` target after the loop
(existing agent → that agent; unknown name → NEW). A turn that ends with no dispatch and
a user-facing question is ASK. Anything else is recorded as OTHER and counted wrong.

**Sizes**: 10, 50, 200, 500, 2,000, 20,000. Setups that do not fit the context window
are recorded as "does not fit" from computed token counts, not called. Large B1 cells
are subsampled.

**Metrics**: follow-up accuracy; duplicate rate (should reuse, created new); false-reuse
rate (should create, reused); wrong-action rate on irreversible steps; ask rate when
ambiguous; over-ask rate when clear; prompt tokens per turn; latency p50/p95; cost per turn.

**Discipline**: temperature 0; dev runs on a cheap model; headline runs on the IA's
configured model; budget guard reads per-call usage and OpenRouter prices; results as
JSONL + summary table + charts in README; held-out set run once at the end.

Stretch: 7-day replay (~150 turns) of B0 vs Ours plotting total agents vs open items.

**Tests (pytest)**: registry (normalize, migrate, ids), state derivation from fixture
tool calls, bounded working-set rendering, search ranking (exact ref, collapse,
archived), spawn check paths (duplicate, `create_new`, same-turn sibling, irreversible
guard), archive/reactivate, per-agent lock ordering, `conversation_limit` wiring, and a
scripted fake-LLM behavioural test through the IA tool loop.

## 8. Who builds what

- Claude: core router (5.1–5.6), eval runner and baselines, integration, review.
- Kiro (Opus 5.5 high, parallel, own files under `evals/world/`): world + scenario
  generator against a frozen schema in `evals/schema.py`.
- Kiro or Devin at the end: independent review and test re-run.
- Owner: pastes keys into `.env`, approves this doc, approves any personal-key spend.

## 9. Milestones (about 5.5 hours)

1. Setup: venv, keys, one smoke LLM call through the real prompt (0:20).
2. Core router + unit tests; Kiro builds world/scenarios in parallel (→ 2:00).
3. Runner + baselines; dev runs on cheap model; fix what it shows (→ 3:15).
4. Headline runs on the IA model; charts (→ 4:00).
5. Beyond-the-brief fixes + tests (→ 4:40).
6. README, review, PR into the fork's `main` (→ 5:30).

## 10. Left to the builder

Exact thresholds for the duplicate check and search boosts (tuned on dev scenarios
only); chart styling; scenario wording.
