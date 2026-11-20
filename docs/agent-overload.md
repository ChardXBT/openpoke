# Agent overload: reference-first routing

## The problem, reframed

OpenPoke's Interaction Agent (IA) sees every execution agent ever created, as a bare
name, on every turn, and decides spawn-vs-reuse by exact string match. After weeks of
use that is hundreds or thousands of names.

At least 20 public forks have attacked this (survey: `docs/research/2026-11-20-fork-survey.md`).
Nearly all of them converge on the same answer: give the model richer agent cards,
retrieve the top few, add a search tool and a duplicate guard. Their own write-ups show
where that stops working: the model asks the user on only 2 of 30 truly ambiguous cases,
reuses agents across people and topics, trades wrong reuse for duplicates, and still
loses accuracy as the roster grows.

Those residual errors are **reference errors**: which draft does "send it" mean, which
Alice, is this the old thread or new work. A model guessing from better cards is the wrong
tool for them. Most references can be settled from data the system already has, and what
cannot be settled is genuinely ambiguous, where the right move is to ask, not guess.

**Thesis.** Resolve references in code where the data allows; make "ambiguous, ask the
user" an outcome enforced in code; let the model choose only among what code could not
settle; and keep the prompt bounded by showing **open items**, not history.

**Why the prompt stays bounded.** Open items ≈ new tasks per day × days until done
(Little's Law). 30 tasks a day that close in about 2 days is about 60 open items, whether
the user has 500 agents or 200,000. The working set scales with how busy the user is,
not with how long they have used the product.

## What changed

| Piece | File | What it does |
|---|---|---|
| Agent registry | `server/services/execution/registry.py` | SQLite replaces `roster.json`: stable `#id`s, normalized names (no more "Email to Alice" vs "email to alice" duplicates), refs (contacts, threads, drafts, triggers, subjects), FTS5 index. Migrates `roster.json` once. |
| Open-loop state | `server/services/execution/loops.py` | States derived from what tools actually did: draft created → *waiting on you*; reply sent → *waiting on reply*; trigger → *scheduled*. Captured at tool time (logs truncate ids). Lapses stale waits, archives idle finished agents, never archives trigger owners. |
| Working set | `server/services/execution/working_set.py` | The `<agents>` block: open items (max 25), 5 recent, one "+N more" line. ≤ 31 lines at any roster size. |
| Search | `server/services/execution/search.py` | `search_agents` tool: exact references outrank wording; state and recency weighting; one contact cannot fill the list; FTS syntax-safe. |
| Resolver | `server/services/execution/resolver.py` | Rules in order: explicit (`#id`, thread/draft id, exact name; "invoice #402" is not agent #402) → inbound email (thread owner, else the item waiting on the sender) → short references ("send it", "did she reply?") against items Poke just surfaced → person named in the message, topic words break ties. Never raises. |
| Ambiguity contract | `server/agents/interaction_agent/tools.py` | `send_message_to_agent` refuses irreversible steps on ambiguous references (`needs_user_choice`), challenges a send aimed away from what the user meant (`confirm_target`), redirects work an agent already owns (`owned_by`), flags look-alike open work (`possible_duplicate`, overridable with `create_new`). |
| Prompt | `server/agents/interaction_agent/routing_prompt.md`, `agent.py` | Explains the agents view, `<routing_hint>`, `<related_agents>` and the tool's answers; replaces upstream's "reuse even if the name is unrelated". |
| Email events | `server/services/gmail/importance_watcher.py` | Notifications carry thread id and sender, so a reply wakes the agent that owns the conversation. |
| Legacy mode | `OPENPOKE_ROUTER=legacy` | Byte-identical upstream prompt, tools and behaviour (tested). |

## Beyond the brief (fixed in code)

- **Two runs on one agent at once** interleaved its memory and could double-send: runs on the same agent are now serialized, across every batch manager including the trigger scheduler's (`batch_manager.py`).
- **Execution agents replayed their entire history** on every call (`conversation_limit` existed but was never passed): now bounded to the last 20 requests (`OPENPOKE_EXEC_HISTORY_LIMIT`).
- **No `max_tokens`** on any LLM call: OpenRouter reserves the model's full output allowance per call; now 2048.
- **OpenPoke did not run on Windows** (`fcntl`): portable lock.
- **The configured model was retired**: `anthropic/claude-sonnet-4` is no longer served on OpenRouter; default is `anthropic/claude-sonnet-5.5` (`OPENPOKE_MODEL`).
- **Alerts spawned new agents** for replies to existing threads: email events now route to the thread's owner.
- **Rescheduling left stale notes**: found in a live run ("push it to 7pm" reused the right agent, the note still said 18:00); fixed.

Found but not fixed (sketched in "Next steps"): triggers can fire twice under multiple
workers or a crash mid-run (no claim/lease; next fire is written after completion); one
global result batch makes fast answers wait for slow agents; the trigger query cannot use
its index; the email watcher polls; storage is single-tenant.

## How it was tested

**Unit and behavioural tests** (offline, no keys): `python -m pytest`. They cover the
registry (migration, odd names, parallel writes), state derivation, search (FTS syntax
safety, collapsing), every resolver rule, every guard path, legacy byte-identity, and a
scripted-model test through the real Interaction Agent runtime.

**Live evaluation** (`evals/`): the real IA prompt and tool schemas, a live model, Gmail
stubbed. Each scenario runs in its own in-memory copy of a synthetic world, so tool calls
never leak between scenarios.

- **Worlds** (`evals/world/generate.py`): 30 core agents plus realistic filler up to
  20,000 agents; collision families (two Alices; one Alice with four live topics; two
  clients at one company; near-name distractors); 90 days of history; filler can never
  invalidate a gold label (checked by a test).
- **Scenarios** (`evals/world/scenarios.py`): 10 families, 78 dev and 31 held-out from a
  separate seed with different wording: needle by paraphrase, same person/other topic,
  "send it"/"did she reply", truly ambiguous (gold: ask), looks ambiguous but is clear
  (gold: act; over-ask check), new task with look-alikes (gold: new agent), cross-client,
  inbound email on a waiting thread, archived agent, repeat reference after a clarification.
- **Setups**: B0 OpenPoke today (names only, upstream prompt); B1 the obvious fix (every
  agent with a description); B2 the common fork answer (cards + BM25/embedding retrieval +
  recent cache + search tool + duplicate guard), built and tuned with the same care; Ours;
  ablations of Ours.
- **Metrics**: correct decision rate with 95% bootstrap intervals; duplicate, wrong-reuse
  and false-reuse rates; asked-when-ambiguous and over-asked rates; prompt tokens, latency
  and cost per turn; paired differences on identical scenarios.

## Results

_Filled in from `evals/results/` after the headline runs._

## Assumptions

- Single user (as upstream). Multi-tenant scale is a next step, not built.
- Synthetic worlds and scenarios; no real inbox was replayed. Mitigations: held-out split
  from a different seed and wording, a strong common-answer baseline, ablations.
- "Ask" is graded correct only when the reference is genuinely ambiguous and the next step
  cannot be undone; asking when the answer was clear is counted as over-asking.

## Next steps

- Trigger claim/lease so a reminder fires once across workers and restarts.
- Per-turn result batches so fast answers do not wait for slow agents.
- Prompt-caching layout: stable system prompt and working set first, the turn last.
- Learn from clarifications: store "the lunch one" → #40 as an alias so the same phrase
  resolves next time (scaffolded: `alias` refs are already indexed and matched).
- Multi-tenant storage (Postgres, tenant-partitioned refs and FTS) and push-based Gmail.
