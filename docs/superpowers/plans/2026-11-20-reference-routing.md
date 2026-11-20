# Reference-First Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OpenPoke's full-roster routing with a bounded open-loop working set, a code-level reference resolver and an ambiguity contract, and prove it with a live-model evaluation against today's OpenPoke and the common fork answer.

**Architecture:** A SQLite agent registry (stable IDs, states, refs captured at tool time) feeds three things: the `<agents>` working set in the IA prompt, a `search_agents` tool, and a resolver that turns each incoming message or email event into `resolved | ambiguous | unresolved`. `send_message_to_agent` enforces the resolver's verdict for irreversible actions and blocks duplicates of thread-owned work. An offline-capable eval harness drives the real IA prompt with a live model over synthetic worlds of 10–20,000 agents.

**Tech Stack:** Python 3.14 (Windows), FastAPI app as-is, sqlite3 with FTS5, httpx, pytest; evals add matplotlib.

**Spec:** `docs/superpowers/specs/2026-11-20-agent-overload-design.md` (+ `docs/superpowers/plans/2026-11-20-build-prompt.md` for rules and owners)

## Global Constraints

- Router mode: `OPENPOKE_ROUTER=reference` (default) or `legacy` (upstream behaviour, unchanged).
- Working set: open items capped at 25, plus up to 5 recent others, plus one count line.
- Archive: `done`/`failed`, idle ≥ 14 days, no active trigger. `waiting_on_others` lapses to `done` after 7 days.
- Search: default limit 8; max 2 hits per contact; recency half-life 30 days.
- Execution history limit: 20 requests (`OPENPOKE_EXEC_HISTORY_LIMIT`).
- Eval budget: `OPENPOKE_EVAL_BUDGET_USD` (35); every LLM call sets `max_tokens` and `temperature=0`.
- Agent names stay the key for logs and triggers.
- Cards and notes are built only from IA-authored instructions and structured tool fields, never email bodies.
- Tests run offline: no network, no keys. Everything runs on Windows.
- No code or data from other forks.
- Commit bodies end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Agent names with quotes, `<`, `&`, emoji or 300+ chars → stored intact, rendered escaped, normalized key stable. (Task 2, Task 4)
2. Search queries with FTS5 syntax (`"`, `-`, `*`, `#402`, `AND`, `NOT`, empty string) → no sqlite error, sensible hits. (Task 4)
3. Existing install upgrading: `roster.json` + logs present, no db; or corrupt `roster.json` → migration keeps every name, never wipes. (Task 2)
4. Parallel execution runs writing to the registry at once → no "database is locked" failures. (Task 2)
5. A message naming an email/thread that belongs to an archived agent, or to no agent → resolver returns resolved+reactivates, or unresolved; never raises. (Task 5)

## File Structure

| File | Responsibility | Owner |
|---|---|---|
| `server/config.py` (modify) | `router_mode`, `execution_history_limit` settings | Claude |
| `server/services/execution/registry.py` | SQLite registry: records, refs, FTS, migration | Claude |
| `server/services/execution/loops.py` | State derivation from tool calls; lapse/archive | Claude |
| `server/services/execution/search.py` | `search_agents` ranking and collapse | Claude |
| `server/services/execution/working_set.py` | `<agents>` block rendering | Claude |
| `server/services/execution/resolver.py` | Reference resolver + irreversible/anaphora detection | Claude |
| `server/agents/interaction_agent/turn_context.py` | Per-turn context (resolution, created names, event) | Claude |
| `server/agents/interaction_agent/{agent.py,tools.py,runtime.py,routing_prompt.md}` (modify/create) | Integration, guards, `search_agents` tool, prompt | Claude |
| `server/agents/execution_agent/runtime.py` (modify) | Tool-call hook into loops | Claude |
| `server/services/gmail/importance_watcher.py` (modify) | Pass email metadata with notifications | Claude |
| `server/agents/execution_agent/{batch_manager.py,agent.py}`, `server/openrouter_client/client.py`, `server/services/execution/roster.py` (modify) | Beyond-brief fixes | Helper B |
| `evals/schema.py` | Shared eval contract (frozen after Task 1) | Claude |
| `evals/world/{generate.py,scenarios.py}` | Synthetic worlds and scenarios | Helper A (Kiro) |
| `evals/harness/{llm.py,transcript.py,loader.py,grade.py,runner.py,report.py}` | Live eval harness | Claude |
| `evals/setups/{upstream/,b0_names.py,b1_cards.py,ours.py}` | Setups B0, B1, Ours | Claude |
| `evals/setups/b2_common.py` | Common-fork-answer baseline | Helper C |
| `tests/...` | Mirrors the above | Owner of each module |

---

### Task 1: Setup, settings and the eval contract

**Files:**
- Modify: `server/config.py` (Settings)
- Create: `evals/__init__.py`, `evals/schema.py`, `evals/requirements.txt`, `tests/__init__.py`, `pytest.ini`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.router_mode: str` (env `OPENPOKE_ROUTER`, default `"reference"`), `Settings.execution_history_limit: int` (env `OPENPOKE_EXEC_HISTORY_LIMIT`, default 20); everything in `evals/schema.py` below.

- [ ] **Step 1: Create venv and install**

Run: `py -3.14 -m venv .venv && .venv/Scripts/python -m pip install -r server/requirements.txt pytest`
Expected: installs; if `composio` fails on 3.14, install the rest and note it (Gmail is stubbed in evals). If importing `server.agents.interaction_agent.tools` then fails on composio, make that import lazy inside `server/services/gmail/client.py`.

- [ ] **Step 2: Check models**

Query `https://openrouter.ai/api/v1/models`; confirm `anthropic/claude-sonnet-4` is served (else pick the closest Sonnet and record it) and pick a cheap tool-calling dev model. Save prices to `evals/.cache/pricing.json` (git-ignored).

- [ ] **Step 3: Write failing test** `tests/test_config.py::test_router_mode_defaults_and_env` — default `"reference"`; with `OPENPOKE_ROUTER=legacy` a fresh `Settings()` gives `"legacy"`; `execution_history_limit` default 20.

- [ ] **Step 4: Add the two settings to `server/config.py`; run test → PASS.**

- [ ] **Step 5: Write `evals/schema.py` exactly as below (the frozen contract for Helpers A and C):**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol

State = Literal["running", "waiting_on_user", "waiting_on_others", "scheduled", "done", "failed"]
RefKind = Literal["contact", "contact_name", "thread", "draft", "trigger", "subject", "alias"]

@dataclass(frozen=True)
class Contact:
    id: str            # "c01"
    name: str          # "Alice Chen"
    email: str         # "alice.chen@acme.com"
    company: str

@dataclass
class WorldAgent:
    id: int                         # 1..N, stable; core agents use the lowest ids
    name: str                       # as the IA would have named it
    topic: str                      # the IA's first instruction, one line
    created_at: str                 # UTC ISO 8601, "Z"
    last_active_at: str
    state: State
    state_note: str                 # one line, no email bodies
    refs: list[tuple[RefKind, str]] = field(default_factory=list)
    archived: bool = False
    has_active_trigger: bool = False
    core: bool = False              # True if any scenario depends on it

@dataclass
class Turn:
    role: Literal["user", "poke", "agent"]   # user_message / poke_reply / agent_message
    text: str                                # agent turns: response text only
    ts: str                                  # UTC ISO
    agent_id: Optional[int] = None           # set for role == "agent"
    status: Literal["SUCCESS", "FAILED"] = "SUCCESS"

@dataclass
class EmailEvent:
    thread_id: str
    sender: str            # email address
    subject: str
    summary: str           # what the importance classifier would write

@dataclass
class Gold:
    kind: Literal["agent", "NEW", "ASK"]
    agent_id: Optional[int] = None
    acceptable: list[int] = field(default_factory=list)   # other agent ids also correct

@dataclass
class Scenario:
    id: str                     # "dev-03-007"
    family: int                 # 1..10, spec section 7
    split: Literal["dev", "heldout"]
    history: list[Turn]
    message: Optional[str]      # user message, or None when event is set
    event: Optional[EmailEvent]
    gold: Gold
    irreversible: bool
    note: str = ""              # why the gold is what it is

@dataclass
class World:
    size: int
    seed: int
    now: str                    # UTC ISO; all ages relative to this
    contacts: list[Contact]
    agents: list[WorldAgent]    # len == size

@dataclass
class PreparedTurn:
    system_prompt: str
    messages: list[dict]
    tools: list[dict]
    handle_tool: Callable[[str, dict], dict]      # tool name, args -> JSON-able result
    resolve_target: Callable[[str], Optional[int]]  # agent_name or "#id" -> world agent id
    prompt_chars: int

class Setup(Protocol):
    name: str
    def prepare(self, world: World, scenario: Scenario, workdir: Path) -> PreparedTurn: ...
```

`evals/requirements.txt`: `matplotlib`, `numpy`. `pytest.ini`: `testpaths = tests`, `addopts = -q -m "not live"`, marker `live`.

- [ ] **Step 6: Smoke call** — a throwaway script (not committed) builds the upstream prompt via `prepare_message_with_history("find my last email from Bob", "", "user")` and calls the dev model with `get_tool_schemas()`; expect a tool call in the response.

- [ ] **Step 7: Commit** `chore: settings for router mode, eval contract`.

---

### Task 2: Agent registry

**Files:**
- Create: `server/services/execution/registry.py`
- Modify: `server/services/execution/__init__.py` (export)
- Test: `tests/execution/test_registry.py`

**Interfaces:**
- Produces:
  - `normalize_name(name: str) -> str` — casefold, collapse non-alphanumerics to one space, strip.
  - `@dataclass AgentRecord(id: int, name: str, name_key: str, created_at: str, last_active_at: str, state: str, state_note: str, topic: str, request_count: int, archived: bool, has_active_trigger: bool)`
  - `@dataclass Ref(kind: str, value: str)`
  - `class AgentRegistry(db_path: Path, legacy_roster_path: Optional[Path] = None, clock: Callable[[], datetime] = utc_now)` with:
    `get_or_create(name: str, topic: str = "") -> tuple[AgentRecord, bool]`,
    `find(ref: str) -> Optional[AgentRecord]` (accepts `#12`, exact name, normalized name),
    `get(agent_id: int) -> Optional[AgentRecord]`,
    `touch(agent_id: int, *, state: Optional[str] = None, note: Optional[str] = None, count_request: bool = False) -> None` (sets `last_active_at`, un-archives),
    `add_ref(agent_id: int, kind: str, value: str) -> None` (idempotent; values lowercased for `contact`),
    `refs(agent_id: int) -> list[Ref]`,
    `owners(kind: str, value: str, include_archived: bool = True) -> list[AgentRecord]`,
    `set_trigger_flag(agent_id: int, active: bool) -> None`,
    `open_items(limit: int = 25) -> list[AgentRecord]` (state not in {done}, not archived, most recent first),
    `recent(limit: int = 5, exclude: set[int] = frozenset()) -> list[AgentRecord]`,
    `count(include_archived: bool = True) -> int`,
    `bulk_insert(records: list[AgentRecord], refs: dict[int, list[Ref]]) -> None` (evals; one transaction),
    `connection() -> sqlite3.Connection` (for search.py),
    `names() -> list[str]`.
  - `get_agent_registry() -> AgentRegistry`, `set_agent_registry(reg: AgentRegistry) -> None` (tests/evals).
  - Tables `agents`, `agent_refs`, FTS5 `agents_fts(name, topic, state_note, refs_text)` kept in sync on every write.

- [ ] **Step 1: Write failing tests** in `tests/execution/test_registry.py`:
  - `test_get_or_create_normalizes`: `get_or_create("Email to Alice")` then `get_or_create("email  to alice!")` → same id, second returns `created=False`.
  - `test_find_by_hash_id_and_name`: `find("#1")`, `find("Email to Alice")`, `find("EMAIL TO ALICE")` → id 1; `find("#999")` → None.
  - `test_migrates_legacy_roster`: tmp `roster.json` `["A", "B"]` → registry has both; corrupt JSON file → registry empty, file left untouched.
  - `test_odd_names_round_trip`: names `'He said "hi" <b>&'`, `"🍣 Tokyo"`, `"x" * 300` stored and found intact.
  - `test_refs_and_owners`: `add_ref(1, "contact", "Alice@Acme.com")` twice → one ref, value lowercased; `owners("contact", "alice@acme.com")` → [agent 1].
  - `test_open_items_and_recent`: states mixed → `open_items` excludes `done` and archived, ordered by `last_active_at` desc; `recent(exclude=…)` respects exclusion.
  - `test_touch_unarchives`.
  - `test_parallel_writes`: 8 threads × 50 `touch`/`add_ref` calls → no exception, counts correct.

- [ ] **Step 2: Run** `pytest tests/execution/test_registry.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** — WAL mode, `timeout=30`, one connection per call guarded by a `threading.Lock`; FTS row rebuilt from the record + refs on each write.

- [ ] **Step 4: Run tests → PASS.** **Step 5: Commit** `feat(registry): sqlite agent registry with ids, refs and fts`.

---

### Task 3: Open-loop state and the execution hook

**Files:**
- Create: `server/services/execution/loops.py`
- Modify: `server/agents/execution_agent/runtime.py` (call hook at run start, after each tool, at end)
- Test: `tests/execution/test_loops.py`

**Interfaces:**
- Consumes: `AgentRegistry` (Task 2).
- Produces: `begin_run(reg, name: str, instructions: str) -> int` (get_or_create, `state="running"`, count request, returns id); `observe_tool_call(reg, agent_id: int, tool: str, args: dict, result: Any, success: bool) -> None`; `finish_run(reg, agent_id: int, success: bool) -> None`; `sweep(reg, now: datetime) -> int` (lapse `waiting_on_others` > 7 d to `done`; archive per Global Constraints; returns rows changed).

- [ ] **Step 1: Failing tests** (`tests/execution/test_loops.py`), each driving fixture tool calls:
  - `test_draft_sets_waiting_on_user`: `gmail_create_draft` ok with `{"recipient_email": "alice@acme.com", "subject": "Q3 deck v2"}` → state `waiting_on_user`, note contains `alice@acme.com` and `Q3 deck v2`, refs contact+subject (+draft id if result has `id`).
  - `test_send_sets_waiting_on_others`: `gmail_reply_to_thread` ok with `thread_id` → `waiting_on_others`, ref thread.
  - `test_trigger_sets_scheduled_and_flag`; `test_paused_trigger_clears_flag`.
  - `test_plain_run_finishes_done`; `test_failed_run_sets_failed`; `test_failed_tool_call_sets_nothing`.
  - `test_note_never_contains_email_body`: result payload with a `body`/`messageText` field → not in note.
  - `test_sweep_lapses_and_archives`: clock at +8 d / +15 d → lapse and archive as specified; agent with active trigger never archived.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement**; in `ExecutionAgentRuntime.execute`, call `begin_run` before the loop, `observe_tool_call` after each `_execute_tool`, `finish_run` in both exit paths; wrap hook calls so a registry error never fails the agent (log it).
- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(loops): derive open-loop state from tool calls`.

---

### Task 4: Search and working set

**Files:**
- Create: `server/services/execution/search.py`, `server/services/execution/working_set.py`
- Test: `tests/execution/test_search.py`, `tests/execution/test_working_set.py`

**Interfaces:**
- Consumes: Task 2, Task 3 (`sweep`).
- Produces: `@dataclass SearchHit(agent: AgentRecord, score: float, hidden_for_contact: int)`; `search_agents(reg, query: str, include_archived: bool = False, limit: int = 8, now: Optional[datetime] = None) -> list[SearchHit]`; `format_card(reg, rec: AgentRecord, now: datetime) -> str`; `render_working_set(reg, now: Optional[datetime] = None) -> str` (runs `sweep` first; returns `"None"` when empty).

- [ ] **Step 1: Failing tests:**
  - `test_exact_ref_beats_topic`: query `alice@acme.com` ranks the agent holding that contact first.
  - `test_topic_match`: "ramen tokyo" finds "Tokyo restaurant search" (topic mentions ramen).
  - `test_fts_syntax_is_safe`: queries `'"'`, `"-"`, `"*"`, `"#402"`, `"AND"`, `"NOT alice"`, `""` → no exception; `#402` finds the agent with subject "Invoice #402".
  - `test_collapse_per_contact`: 6 agents for one contact → at most 2 hits, `hidden_for_contact == 4` on the last shown.
  - `test_archived_excluded_unless_requested`.
  - `test_working_set_bounded`: 20,000 agents with 40 open → output has ≤ 31 lines; contains the count line with `19,970` (20,000 − 25 − 5).
  - `test_card_format`: matches `#412 Email to Alice – Q3 deck · waiting on you: … · 2h ago` shape; names with `<`/`&` are escaped.
  - `test_empty_registry_renders_none`.
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement** — FTS5 query built from extracted word tokens each quoted (`"tok"*` prefix), OR-joined; exact-ref boost by looking up emails/`#\d+`/subjects in `agent_refs`; score = bm25 rank fused with recency and state weights.
- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(search): agent search and bounded working set`.

---

### Task 5: Reference resolver

**Files:**
- Create: `server/services/execution/resolver.py`
- Test: `tests/execution/test_resolver.py`

**Interfaces:**
- Consumes: Tasks 2 and 4.
- Produces: `@dataclass EventMeta(kind: str, thread_id: Optional[str], sender: Optional[str], subject: Optional[str])`; `@dataclass Resolution(kind: Literal["resolved","ambiguous","unresolved"], candidates: list[int], reason: str, rule: str)`; `resolve(reg, text: str, transcript: str, event: Optional[EventMeta] = None) -> Resolution`; `surfaced_agents(reg, transcript: str, turns: int = 4) -> list[int]` (agent ids from the last `turns` `<agent_message>` entries, newest first, parsed from `[SUCCESS|FAILED] <name>:` lines); `is_irreversible(instructions: str) -> bool`; `is_reference_message(text: str) -> bool`; `render_hint(reg, res: Resolution) -> str` (`""` when unresolved).

- [ ] **Step 1: Failing tests**, one per rule plus negatives:
  - `test_rule1_hash_id` / `test_rule1_email_in_text` / `test_rule1_exact_name`.
  - `test_rule2_event_thread_owner`; `test_rule2_event_sender_waiting`; `test_rule2_unknown_thread_unresolved`.
  - `test_rule3_send_it_one_draft` → resolved to the one `waiting_on_user` agent surfaced in the transcript.
  - `test_rule3_send_it_two_drafts` → ambiguous, both ids.
  - `test_rule3_did_she_reply` → the surfaced `waiting_on_others` agent.
  - `test_rule4_one_live_matter` / `test_rule4_two_live_matters_ambiguous` (same contact, two open topics).
  - `test_archived_owner_resolved` (archived agent holding the thread → resolved).
  - `test_long_new_request_unresolved` ("find me a dentist in Toronto with weekend hours" → unresolved).
  - `test_is_irreversible`: "send it", "forward this to Bob", "reply yes", "cancel the reminder", "delete the draft" → True; "draft a reply", "find", "look up" → False.
  - `test_hint_text`: resolved → contains `#id` and reason; ambiguous → contains "ask".
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement** rules in spec 5.5 order; contact-name matching uses `contact_name` refs and email local parts.
- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(resolver): resolve agent references in code`.

---

### Task 6: Ambiguity contract, tools, prompt, integration

**Files:**
- Create: `server/agents/interaction_agent/turn_context.py`, `server/agents/interaction_agent/routing_prompt.md`
- Modify: `server/agents/interaction_agent/{agent.py,tools.py,runtime.py}`, `server/services/gmail/importance_watcher.py`
- Test: `tests/interaction/test_guards.py`, `tests/interaction/test_prompt.py`, `tests/interaction/test_turn_flow.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - `TurnContext(resolution: Optional[Resolution], event: Optional[EventMeta], created: set[str])`; `current_turn: ContextVar[Optional[TurnContext]]`.
  - `tools.set_dispatcher(fn: Callable[[str, str], None]) -> None` (default: batch manager; evals/tests: recorder).
  - `send_message_to_agent(agent_name: str, instructions: str, create_new: bool = False) -> ToolResult` statuses: `submitted`, `needs_user_choice` (payload `options: list[str]` cards), `confirm_target`, `owned_by` (payload `agent: "#id name"`), `possible_duplicate` (payload `candidates: list[str]`).
  - `search_agents` tool schema + handler returning cards.
  - `prepare_message_with_history(latest_text, transcript, message_type="user", event: Optional[EventMeta] = None)` adds `<routing_hint>` in reference mode.
  - `InteractionAgentRuntime.handle_agent_message(agent_message: str, event: Optional[EventMeta] = None)`.
  - In legacy mode, every function behaves exactly as upstream.
- [ ] **Step 1: Failing tests** (`test_guards.py`, recorder dispatcher, tmp registry):
  - `test_ambiguous_irreversible_blocked` → `needs_user_choice`, no dispatch.
  - `test_ambiguous_reversible_dispatched`.
  - `test_resolved_single_not_blocked` (no over-ask).
  - `test_irreversible_to_non_waiting_target_confirms` while another agent is `waiting_on_user`.
  - `test_thread_owned_spawn_redirects` → `owned_by`, no new agent.
  - `test_possible_duplicate_then_create_new`.
  - `test_same_turn_sibling_not_flagged`.
  - `test_hash_id_and_normalized_name_reuse`.
  - `test_legacy_mode_unchanged`: `OPENPOKE_ROUTER=legacy` → names-only `<active_agents>` block and upstream tool schemas.
- [ ] **Step 2: Failing tests** (`test_prompt.py`): reference mode prompt contains `<agents>` explanation and no "Don't worry if the agent name is unrelated"; message contains `<routing_hint>` when resolved; legacy prompt byte-identical to upstream.
- [ ] **Step 3: Failing test** (`test_turn_flow.py`): a scripted fake LLM (patched `request_chat_completion`) returns `send_message_to_agent("Email to Alice", "send it")` with two drafts surfaced → the tool result fed back is `needs_user_choice`; the scripted second response asks the user; no dispatch recorded.
- [ ] **Step 4: Run all → FAIL.** **Step 5: Implement**; watcher passes `EventMeta("email", email.thread_id, sender_address, email.subject)` to `handle_agent_message`.
- [ ] **Step 6: Run full suite → PASS.** **Step 7: Commit** `feat(routing): ambiguity contract, search tool, routing hint`.

---

### Task 7: World and scenario generator (Helper A: Kiro, Opus 5.5 high)

**Files:** Create `evals/world/__init__.py`, `evals/world/generate.py`, `evals/world/scenarios.py`; Test `tests/evals/test_world.py`. Touch nothing else; do not edit `evals/schema.py`.

**Interfaces:**
- Consumes: `evals/schema.py`.
- Produces: `build_world(size: int, seed: int) -> World` (core agents identical for a seed at every size; filler fills to `size`; 20,000 in < 10 s); `build_scenarios(world: World) -> list[Scenario]` (uses only core agents; split = `"dev"` for seed 1, `"heldout"` for seed 2); CLI `python -m evals.world.generate --size N --seed S` prints counts by state and family.

Content per spec section 7: ~40 contacts with the collision families; ~30 archetypes; IA-style names; families 1–10 (~80 dev, ~30 held-out; families 4 and 5 balanced); histories end with the surfaced agent results the family needs; every agent `Turn` has `agent_id`.

- [ ] **Step 1: Failing tests**: `test_deterministic`, `test_sizes` (10, 1,000, 20,000 lengths), `test_core_stable_across_sizes`, `test_gold_agents_exist`, `test_family_counts`, `test_splits_disjoint` (no shared scenario text), `test_twenty_k_under_10s`.
- [ ] **Step 2–4: Implement until PASS.** **Step 5: Commit** `feat(evals): synthetic worlds and scenarios`.

---

### Task 8: Beyond-the-brief fixes (Helper B)

**Files:** Modify `server/agents/execution_agent/batch_manager.py`, `server/agents/execution_agent/agent.py`, `server/openrouter_client/client.py`, `server/services/execution/roster.py`; Test `tests/test_beyond_brief.py`. Touch nothing else.

**Interfaces:** Consumes `Settings.execution_history_limit` (Task 1). Produces: per-agent `asyncio.Lock` serialization in `ExecutionBatchManager.execute_agent`; `ExecutionAgent(name, conversation_limit=None)` defaults to `get_settings().execution_history_limit` when `None`; `request_chat_completion(..., max_tokens: int = 2048)` always sends `max_tokens`; `roster.py` imports `fcntl` only when available (`msvcrt.locking` fallback, else no lock).

- [ ] **Step 1: Failing tests**: `test_same_agent_runs_serialize` (two concurrent `execute_agent` on one name with a patched slow runtime → second starts after first ends; different names overlap); `test_history_limit_default` (25 requests logged → prompt holds the last 20); `test_max_tokens_sent` (patched httpx captures payload); `test_roster_imports_without_fcntl`.
- [ ] **Step 2–4: Implement until PASS.** **Step 5: Commit** `fix: serialize runs per agent, bound history, set max_tokens, portable lock`.

---

### Task 9: Eval harness core and setups B0, B1, Ours

**Files:**
- Create: `evals/harness/{__init__.py,llm.py,transcript.py,loader.py,grade.py,runner.py}`, `evals/setups/{__init__.py,b0_names.py,b1_cards.py,ours.py}`, `evals/setups/upstream/{system_prompt.md,tools.json}` (from `git show 5b5f635:…`)
- Test: `tests/evals/test_harness.py`

**Interfaces:**
- Consumes: schema (Task 1), registry/resolver/tools (Tasks 2–6), world (Task 7, stub world in tests).
- Produces:
  - `Budget(cap_usd: float, ledger: Path)` with `spent() -> float`, `check(est_usd: float) -> None` (raises `BudgetExceeded`), `charge(usd: float) -> None`.
  - `LLMClient(model: str, budget: Budget, max_tokens: int = 1024)` with `async complete(system: str, messages: list[dict], tools: list[dict]) -> LLMResult(message: dict, prompt_tokens: int, completion_tokens: int, cost_usd: float, latency_s: float)`; 3 retries on transport errors.
  - `render_history(turns: list[Turn], world: World) -> str` in OpenPoke's transcript format (`<user_message timestamp=…>`, `<poke_reply …>`, `<agent_message …>[SUCCESS] <name>: <text>`).
  - `load_world(world: World, db_path: Path) -> AgentRegistry` (bulk insert).
  - `@dataclass Decision(kind: Literal["agent","NEW","ASK","OTHER"], agent_id: Optional[int], dispatches: int, asked: bool)`; `extract_decision(calls: list[dict], user_msgs: list[str], resolve_target) -> Decision`; `grade(decision, scenario) -> str` (`correct|duplicate|wrong_reuse|missed_ask|over_ask|other`); `summarize(rows) -> dict` with 95% bootstrap CIs (1,000 resamples, seeded) and `paired_delta(rows_a, rows_b) -> (delta, lo, hi)`.
  - `run(setup: Setup, split: str, sizes: list[int], model: str, out: Path, limit: Optional[int] = None) -> Path` (JSONL rows: scenario id, family, size, setup, model, decision, outcome, prompt_tokens, latency, cost); CLI `python -m evals.harness.runner --setup ours --split dev --sizes 10,200,1000 --model <id>`; skips cells whose estimated prompt exceeds the model's context and writes `outcome="does_not_fit"`.
  - Setups `B0Names`, `B1Cards`, `Ours(ablate: set[str] = set())` with ablations `{"resolver","contract","states"}`.
- [ ] **Step 1: Failing tests** (offline, fake `LLMClient`): `test_budget_blocks_over_cap`; `test_render_history_format`; `test_extract_decision_agent_new_ask_other`; `test_grade_outcomes` (all six); `test_bootstrap_ci_deterministic`; `test_ours_prepare_uses_real_prompt` (system prompt contains the routing section; tools include `search_agents`); `test_b0_matches_upstream_prompt`; `test_does_not_fit_skipped_without_call`.
- [ ] **Step 2–4: Implement until PASS.** **Step 5: Live check** (`-m live`): one dev scenario per setup at size 10 on the dev model. **Step 6: Commit** `feat(evals): harness, grading, setups`.

---

### Task 10: Common-fork-answer baseline B2 (Helper C)

**Files:** Create `evals/setups/b2_common.py`; Test `tests/evals/test_b2.py`. Touch nothing else.

**Interfaces:** Consumes `evals/schema.py` (`Setup`, `PreparedTurn`, `World`, `Scenario`) and `evals/setups/upstream/` (Task 9). Produces `B2Common(k: int = 12, recent: int = 5, embeddings: bool = True)` implementing `Setup`.

Behaviour: upstream system prompt plus a short card-usage section; the agents block = top-k cards for the message (BM25 over name+topic+note+refs, fused by RRF with OpenAI `text-embedding-3-small` cosine when `embeddings=True`, vectors cached on disk under `evals/.cache/`) plus the `recent` most recently active; a `search_agents(query)` tool over the same index; `send_message_to_agent` accepts `#id` and returns `possible_duplicate` when a new name's top match scores above a threshold tuned on dev. No resolver, no ambiguity contract, no states beyond the card note.

- [ ] **Step 1: Failing tests** (offline, embeddings off): `test_topk_contains_gold_for_needle`, `test_cards_bounded`, `test_search_tool_returns_cards`, `test_duplicate_guard_triggers`, `test_hash_id_resolves`.
- [ ] **Step 2–4: Implement until PASS.** **Step 5: Commit** `feat(evals): common-answer baseline`.

---

### Task 11: Dev runs, fixes, headline runs, report

**Files:** Create `evals/harness/report.py`, `evals/results/` (committed JSONL + summary), `docs/figures/*.png`; Test `tests/evals/test_report.py`.

**Interfaces:** Consumes Task 9 runner rows. Produces `python -m evals.harness.report evals/results/<run>` → `summary.md` (tables per setup × size × metric with CIs, paired deltas Ours−B2 and Ours−B0) and three charts (accuracy vs size; clarify recall vs over-ask; prompt tokens vs size).

- [ ] **Step 1: Failing test** `test_report_from_fixture_rows` (fixture JSONL → summary has every setup and size; charts written).
- [ ] **Step 2: Implement → PASS.**
- [ ] **Step 3: Dev runs** on the dev model: all setups at 10/200/1,000. Read failures by family; fix causes in code (not scenarios); re-run. Log each change and its effect in `evals/results/JOURNAL.md`.
- [ ] **Step 4: Freeze** — `sha256` of the held-out scenarios written to `evals/results/heldout.sha256` and committed before Ours runs on held-out.
- [ ] **Step 5: Headline runs** on the IA model: all setups, held-out, sizes 10/50/200/1,000/5,000/20,000 (B1 subsampled at ≥ 1,000); second model on Ours vs B2 at 1,000. Watch the budget ledger.
- [ ] **Step 6: Report + commit** `eval: headline results`.

---

### Task 12: README, review, PR

- [ ] **Step 1:** README section "Agent overload: reference-first routing": framing, what changed (with file links), how to run (tests, evals, app), results with CIs and charts, what we tried and cut (and why), beyond-the-brief, next steps (multi-user, triggers lease, prompt caching, fast router), assumptions. Link the survey.
- [ ] **Step 2:** Independent review of the full diff by Devin or Kiro; fix findings with tests.
- [ ] **Step 3:** Full offline suite green on a clean venv; `git diff` key scan.
- [ ] **Step 4:** PR `agent-overload` → fork `main` (after the owner confirms visibility).

### Task 13 (stretch): corrections memory and cross-repo benchmark

- Corrections (spec 5.8): on a user reply to `needs_user_choice`, add an `alias` ref; resolver rule 3/4 consults aliases. Tests: `test_alias_learned_then_resolves`. Eval family 10.
- Cross-repo benchmark: run our held-out scenarios through public forks' routers in isolated, non-networked clones (their code is untrusted; run with `-I`, never with our keys unless the owner approves), or run Ours on their published case sets where the format allows. Report anonymized ("Fork A/B/C") unless the owner decides otherwise.
