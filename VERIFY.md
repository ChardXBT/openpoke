# Clean-machine verification of `agent-overload`

Date: 2026-11-20. Branch verified: `agent-overload` @ `4a351e5` (PR #1).
_Historical record of that commit. The suite has grown since; on 2026-11-26 `main` ran **267 passed, 1 skipped** offline with `python -m pytest` (the default pytest options exclude live tests). The count changes as tests are added, so use that command for the current exact count. The setup steps still apply._
Machine: fresh Linux (Ubuntu 24.04, kernel 6.1, 4 vCPU), Python 3.12.3, Node 22.23.5, npm 10.9.9.
No API keys or tokens in the environment; `.env` was copied from `.env.example` with
its placeholder values only. No LLM or paid API was called; nothing was spent.

**Verdict: PASS.** Everything that can run without an API key reproduces exactly: tests
pass, world generation is fast and deterministic, both committed reports regenerate
identically from their `rows.jsonl`, every number in `JOURNAL.md` for the two dev runs
matches the rows, the held-out hash recomputes, and the API server answers health.
Gaps: the held-out run described in `JOURNAL.md` has no committed rows, so its numbers
cannot be checked, and `prompt_sizes.json` has no committed generator script.
The setup docs had four small problems, all fixed on this branch (see "Fixes").

## 1. README top section + `evals/README.md`, from scratch

| # | Command | Result |
|---|---|---|
| 1 | `git clone https://github.com/ChardXBT/openpoke.git ~/work/openpoke && git checkout agent-overload` | OK |
| 2 | `cp .env.example .env` (placeholders only, step 3 "add keys" skipped by design) | OK |
| 3 | `python3.10 -m venv .venv` (README Quickstart step 4) | **FAIL**: exit 127, no `python3.10` on Ubuntu 24.04 |
| 4 | `python3 -m venv .venv` | **FAIL**: `ensurepip is not available` (Debian/Ubuntu need `python3-venv`) |
| 5 | `sudo apt-get install -y python3.12-venv`, then `python3 -m venv .venv && source .venv/bin/activate` | OK, Python 3.12.3 |
| 6 | `pip install -r server/requirements.txt -r evals/requirements.txt pytest` (evals/README) | OK, 23 s |
| 7 | `python -m pytest` (README top) | OK, `182 passed, 4 warnings` (only after step 6: README top section had no install step, and `server/requirements.txt` has no pytest) |
| 8 | `npm install --prefix web` (Quickstart step 6) | OK, 25 s |
| 9 | `python -m server.server` (Quickstart step 7), `curl /api/v1/health` | OK, HTTP 200, stopped |
| 10 | `python -m evals.harness.runner --help` | OK. The live runner itself (README top, evals/README "dev run") was **not run**: it calls OpenRouter. |
| 11 | `python -m evals.harness.report <dir> --out ...` | OK (step 4 below) |
| 12 | `npm run dev --prefix web`, Gmail OAuth (Quickstart 8–9) | Not run: needs Composio keys; out of scope for a no-key check |

Also: README Quickstart step 1 clones upstream `shlokkhemani/OpenPoke`, which does not
contain this fork's work.

After the doc fixes, a second fresh clone following the corrected README top block
verbatim (`venv` → `pip install ...` → `python -m pytest`) gave `182 passed`, and a
report regenerated there was identical.

## 2. Tests

- `python -m pytest -q`: all dots, no failures, **but no count line**: `pytest.ini`
  already sets `addopts = -q`, so `-q` on the command line makes it `-qq`, which hides the summary.
- `python -m pytest`: **182 passed, 4 warnings** (FastAPI `on_event` deprecation) in ~7 s.
- `python -m pytest -o addopts= --co`: 182 collected; `-m live`: 0 selected. No live tests
  exist in the suite, so nothing is skipped by the `not live` marker.

## 3. World generation

`python -m evals.world.generate --size 20000 --seed 1`: exit 0, **0.66–0.73 s wall**
(3 runs), output byte-identical between runs:

```
world size=20000 seed=1 core=30 now=2026-11-20T15:00:00Z
running 0 / waiting_on_user 581 / waiting_on_others 724 / scheduled 1017
done 17016 (14453 archived) / failed 662 (544 archived) / total 20000 (14997 archived)
scenarios split=dev total=78  (families 1–9: 8 each except family 3: 10; family 10: 4)
```

## 4. Committed reports regenerated from `rows.jsonl`

`python -m evals.harness.report evals/results/<dir> --out /tmp/<dir>` for both dirs:

| Dir | summary.md | summary.json | 3 PNGs |
|---|---|---|---|
| `dev-haiku-r1` | byte-identical | equal | pixel-identical (bytes differ: PNG metadata only) |
| `dev-haiku-r2` | byte-identical | equal | pixel-identical (bytes differ: PNG metadata only) |

I also recomputed the numbers from the rows with a separate script that does not use
`evals/harness/grade.py`: correct rate, asked-when-ambiguous (gold ASK correct), over-ask,
wrong reuse, mean prompt tokens and total cost per setup × size. Both runs: 936 rows =
4 setups × 3 sizes × 78, split `dev`, model `anthropic/claude-haiku-5.5`, one rep.

| Claim | Where | Rows say | OK |
|---|---|---|---|
| r1 cost $2.88 | `JOURNAL.md:8` | $2.8796 | ✓ |
| r1 table (B0 95/90/90, B1 94/94/94, B2 94/94/91, Ours 95/94/94; asked 62–75 / 75–88 / 75 / 88–100; tokens 50k/116k/14k/19k) | `JOURNAL.md:12-15` | 74,70,70 / 73,73,73 / 73,73,71 / 74,73,73 of 78; asks 5–6/8, 6–7/8, 6/8, 7–8/8; 50,334 / 116,319 / 14,227 / 18,898 | ✓ |
| r1 Ours zero wrong reuse, over-asked 3–7% | `JOURNAL.md:18` | 0%; 4.3 / 7.1 / 2.9% | ✓ |
| r2 cost $2.89 | `JOURNAL.md:40` | $2.8946 | ✓ |
| r2 table (B0 96/95/91, B1 92/94/92, B2 92/96/92, Ours 99/97/95; asked; wrong reuse 1 / 0–3 / 0–4 / 0; tokens 51k/115k/14k/19k) | `JOURNAL.md:44-47` | 75,74,71 / 72,73,72 / 72,75,72 / 77,76,74 of 78; wrong reuse 1.4 / 0–2.9 / 0–4.3 / 0; 51,009 / 114,880 / 14,402 / 18,842 | ✓ |
| r2 four remaining Ours misses at 1,000 | `JOURNAL.md:52-60` | exactly 4: dev-09-003 Globex (duplicate), dev-01-008 "japan food list" (duplicate), dev-10-001 "which Alice?" (over_ask), dev-06-008 hydro due date (over_ask) | ✓ |
| held-out hash 4249db74… | `JOURNAL.md:62` | matches file | ✓ |
| heldout-haiku numbers ($0.115/turn, 227k tokens, 51/62 turns, latency p50s, "4 of 7" misses) | `JOURNAL.md:64-80` | **no `evals/results/heldout-*` rows committed** | cannot verify |
| 78 dev / 31 held-out, 10 families, up to 20,000 agents | `docs/agent-overload.md:71-76`, `evals/README.md:11-12` | 78 / 31 / 10 / 20,000 | ✓ |
| `max_tokens` now 2048 | `docs/agent-overload.md:49` | `server/openrouter_client/client.py:57` | ✓ |
| Results section | `docs/agent-overload.md:90` (before fix) | placeholder; dev results already existed | stale, fixed |

**No numeric mismatch** between rows and any committed `summary.md`, `JOURNAL.md` or
`docs/agent-overload.md`.

Not regenerable: `evals/results/prompt_sizes.json` ("computed offline", commit `b2ac93f`)
has no script or command in the repo that produces it.

## 5. Held-out hash

`heldout_hash_ok(build_scenarios(build_world(size, 2)))` is **True** at sizes 30, 200,
1,000, 5,000 and 20,000 (31 scenarios each, sha256 `4249db74c1fe82e6…`, same as
`evals/results/heldout.sha256`). Control check: the dev split returns False.

## 6. API server

`python -m uvicorn server.app:app --port 8001` started, then `GET /api/v1/health` returned
**HTTP 200** `{"ok":true,"service":"openpoke","version":"0.3.0"}`. I stopped it with SIGTERM
to its PID and it shut down cleanly (scheduler and watcher stopped). It started fine
with placeholder keys and logged no outbound calls.

## 7. Fixes on this branch (docs only, no product or eval logic changed)

- `README.md` top block: add clone/checkout of this fork, venv creation (with the
  Debian/Ubuntu `python3-venv` note), and `pip install ... pytest` before `python -m pytest`;
  state the expected `182 passed`.
- `README.md` Quickstart step 1: note to clone this fork instead of upstream.
- `README.md` Quickstart step 4: any Python 3.10+ works (`python3 -m venv`); add the `python3-venv` note.
- `evals/README.md`: expected count, and a note that an extra `-q` hides the count line.
- `docs/agent-overload.md` Results: was a bare placeholder; now points to the committed
  dev results and JOURNAL, says r2 is an upper bound, and shows the offline regeneration
  command. The held-out headline stays marked as pending.

## Owner steps

1. Commit the held-out run's `rows.jsonl` (`heldout-haiku`, and `heldout-haiku-scale` once
   it runs) so `JOURNAL.md:64-80` can be checked, or label that section as not yet reproducible.
2. Commit the script or command that produced `evals/results/prompt_sizes.json`.
3. Fill in the `docs/agent-overload.md` Results section with the held-out headline when it exists.
4. Optional: `pytest.ini` `addopts = -q` makes `pytest -q` hide the count line; I left it
   unchanged because it is test config.
5. Optional: replace FastAPI `@app.on_event` with lifespan handlers (the 4 test warnings);
   that is product code, so I did not touch it.
