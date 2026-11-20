# External test cases

Adapters that load routing test cases published by other public OpenPoke forks into our
schema (`evals/schema.py`), so our setups are also measured on tests we did not write.

## Rules

- Forks are untrusted. Adapters only parse their JSON/JSONL data with `json`. No fork code is
  imported, run or copied, and no fork data is committed here.
- Each fork is cloned at a pinned commit under `OPENPOKE_EXTERNAL_SRC/<owner>` (default
  `~/Downloads/openpoke-external-src/<owner>`). `check_pinned` refuses a clone at another commit.

```powershell
$src = "$HOME\Downloads\openpoke-external-src"
git -c core.hooksPath=NUL clone --single-branch -b main https://github.com/KareemElMaaddawy/openpoke-enhanced.git "$src\KareemElMaaddawy"
git -C "$src\KareemElMaaddawy" checkout 372b47191303bbe6585075ff77ff4ae113601344
git -c core.hooksPath=NUL clone --single-branch -b main https://github.com/draggle/openpoke.git "$src\draggle"
git -C "$src\draggle" checkout eae649b3bb2260cbcba86a73edbc50b38b99fd86
```

## Use

```python
from evals.external import factories, load_all
from evals.harness.runner import run

for module, loaded in load_all().items():          # {module: ExternalLoad}
    print(module, loaded.loaded, dict(loaded.skipped))
    for world, scenarios in loaded.worlds:
        world_factory, scenario_factory = factories(world, scenarios)
        await run(setups, "dev", [world.size], client, out / module / str(world.seed),
                  world_factory=world_factory, scenario_factory=scenario_factory)
```

- Each adapter has `load(src_dir) -> list[(World, [Scenario])]` and `load_report(src_dir)`,
  which also returns `skipped` and `adjusted` counters (reason -> cases).
- Pass `split="dev"` to `run`. `split="heldout"` checks our own held-out hash and refuses. The
  rows still record `scenario.split == "heldout"`.
- `run` writes one `summary.json` per output directory, so give each world its own directory
  (as above) and pool `rows.jsonl` afterwards.
- Every World has a unique `(seed, size)`, because every setup caches its registry on that key.
  Seeds are `1_000_000 + case position` (KareemElMaaddawy) and `2_000_001` (draggle).
- Scenario ids are `<owner lowercase>-<their id>`. All scenarios are `split="heldout"` and
  `irreversible=False` (neither fork records a risk flag).

## KareemElMaaddawy/openpoke-enhanced (`kareemelmaaddawy.py`)

- Pinned: `372b47191303bbe6585075ff77ff4ae113601344` (branch `main`, 2026-10-28).
- Read: `evals/agent_overload/fixtures/v1/agents.test.jsonl`, `rosters.test.json`,
  `cases.single.test.jsonl`, `cases.sequence.test.jsonl` (counted only), and the format spec in
  `docs/agent-overload-experiment-design.md`.
- Why: a frozen, hashed test split that they tuned only on dev and ran once with targets set in
  advance. Gold labels come from a drafting model, were cross-labelled by a second model, and
  disagreements were reviewed.

**Mapping**

| Theirs | Ours |
|---|---|
| One single-turn case | One World + one Scenario |
| Roster at N (default 200, their largest) | `required_agents` + first `N - len(required)` of `filler_order` (their documented rule) |
| Agent `name` | `name`, exactly |
| `work_log[0].instructions` / `work_log[-1].result` | `topic` / `state_note` (joined onto one line) |
| No status field | `state="done"`, no refs |
| `agent_times` (required), `filler_age_days` (filler) | `created_at` / `last_active_at`; filler `created_at = last_active_at` |
| Message timestamp | `World.now` |
| History `user_message` / `poke_reply` / `agent_message` | `Turn` user / poke / agent; agent turns resolved by the `[SUCCESS] <name>: ` prefix |
| `email_alert` message | `EmailEvent(thread_id="", sender, subject, summary=alert body)` |
| `REUSE` / `REUSE_ANY` / `CREATE` | `Gold("agent", first)` / `Gold("agent", first, acceptable=rest)` / `Gold("NEW")` |
| `query_style` | family: explicit, paraphrased -> 1; implicit_follow_up -> 3; near_distractor REUSE -> 5; CREATE -> 6; email alerts -> 8; REUSE_ANY -> 0 |

Ids are assigned in creation order, as our registry would, so a gold agent's id says nothing
about its role.

**Counts** (test split, N=200)

| | Cases |
|---|---|
| Loaded | **105** (69 `agent`, 10 `agent` with acceptable alternatives, 26 `NEW`); 105 Worlds of 200 agents |
| Families | 1: 31, 3: 18, 5: 16, 6: 26, 8: 4, 0: 10 |
| Skipped: gold `NO_DELEGATION` (no Gold kind for "answer directly") | 12 |
| Skipped: message is an execution-agent result | 3 |
| Skipped: sequence turns (state carried across turns) | 96 (8 sequences x 12) |
| Loaded with loss: summarized history, `summary_text` dropped | 31 |
| Loaded with loss: email alert has no thread id | 4 |
| Loaded as is: their cross-labeller disagreed, final label kept (noted in `Scenario.note`) | 9 |

**Fidelity limits**

- Their agents have no state or structured refs; draft, thread and contact ids exist only inside
  result text. Our resolver's structured signals (thread/draft owners, waiting states) get
  nothing, so `ours` is measured mostly on its model-side path here.
- Their roster places the anchor agent in a seeded position bucket. That placement lives in their
  code and is not reproduced; our setups order agents by their own rules.
- Summarized cases lose their summary prefix (assembled background). The drafted tail, which holds
  the label-relevant turns, is kept in full.
- Email alert sender and subject are parsed from free text with a regex; the summary is their
  alert body verbatim.
- Timestamps are taken as UTC.

## draggle/openpoke (`draggle.py`)

- Pinned: `eae649b3bb2260cbcba86a73edbc50b38b99fd86` (branch `main`, 2026-11-02).
- Read: `evals/heldout_routing.json`.
- Why: hand-written "in one sitting before any run and never used for tuning", with pass bars
  fixed in the same file. One shared roster of 50 agents.

**Mapping**

| Theirs | Ours |
|---|---|
| The whole file | One World of 50 agents, 72 Scenarios |
| Agent `name` | `name`, exactly |
| `instructions[0]` / `summary` | `topic` / `state_note` |
| `has_trigger` | `state="scheduled"`, `has_active_trigger=True`; every other agent `state="done"` |
| `person` | `("contact_name", person)` ref (first names, as they wrote them) |
| `last_used_days_ago` | `last_active_at = now - days`, `created_at = last_active_at`; `now` is a fixed anchor (`2026-11-01T12:00:00Z`) because they store only relative ages |
| Query `text` / `target` | `message` / `Gold("agent", id of the agent with that name)` |
| Query position (no ids in their file) | id `draggle-q001` ... `draggle-q072` |
| Query `kind` | family: keyword, topic_only, paraphrase -> 1; lookalike -> 2; recency, name_only, nickname, typo, name_opaque -> 0 |

**Counts**: 72 loaded (families 1: 26, 2: 10, 0: 36), 0 skipped.

**Fidelity limits**

- No conversation history. Their `recency` queries ("ok send it") rely only on which agent was
  used last.
- No status field. A summary such as "Draft ready for your approval; not sent." stays
  `state="done"` with the text in `state_note`, which works against state-aware routing.
- `name_only` queries name a person with several agents ("anything from Priya?"); their gold is
  the most recent one. Our design would often ask, which grades as `over_ask`.
- Their one-word `topic`/`desc` fields are not used; `topic` is the first instruction.

## Forks surveyed and not adapted

- `sina158/openpoke` (`gm-takehome`, `5b67526`): `evals/golden/routing_v2_heldout.jsonl` (110
  cases with `reuse`, `create`, `clarify` and `parallel` gold) over a 5,302-agent pool. Their
  per-case roster at N is sampled in code (`roster_seed`), which is not recorded in any data file,
  so it cannot be rebuilt faithfully. It is the only one with `clarify` (ASK) gold.
- `talfigh/openpoke` (`agent-overload`, `2d7d371`): 131 cases in `routing_cases.json` over rosters
  stored as name lists (40-500); descriptions would have to be joined by name from `topics.json`.
  The cases have no dev/test split and were rerun across their tuning iterations
  (`routing_search-v1` to `-v4`).
- `RayanHossain/openpoke` (`main`, `8e22062`): probes and worlds (N=10-50) are generated by code.
  The data files hold run results (gold ids but no message text or roster) and derived artifacts
  (names, summaries, briefs), not self-contained cases.
