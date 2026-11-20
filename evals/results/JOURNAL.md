# Eval journal

Every run, what it showed, and what changed because of it. Dev split only until the
held-out set is hashed; tuning never touches held-out scenarios.

## dev-haiku-r1 (2026-11-20, anthropic/claude-haiku-5.5, 78 dev scenarios, sizes 30/200/1000)

Cost $2.88. Results: `evals/results/dev-haiku-r1/summary.md`.

| Setup | 30 | 200 | 1,000 | Asked when ambiguous | Prompt tokens at 1,000 |
|---|---|---|---|---|---|
| B0 OpenPoke today | 95% | 90% | 90% | 62–75% | 50k |
| B1 every card | 94% | 94% | 94% | 75–88% | 116k |
| B2 common answer | 94% | 94% | 91% | 75% | 14k |
| Ours | 95% | 94% | 94% | 88–100% | 19k |

Read: Haiku 5.5 is strong at these sizes; accuracy intervals overlap. Ours leads on asking
when truly ambiguous and has zero wrong reuse, but over-asked 3–7%.

Ours' errors traced to three resolver causes (fixed, each with a test):
1. "Stop watching flights for the conference w alice" → ambiguous over Alice's 5 open
   items: "conference" did not match "Conf". Fix: shared-prefix word matching; the action
   ("stop") narrows to scheduled items only when words do not decide.
2. "the kestrel workshop email to jen looks good, send it" → resolved to Omar's invoice
   thread (the only item surfaced) even though it is waiting on a reply. Fix: a named
   person beats "just surfaced"; "send" never falls back to an item without a draft.
3. "sure send that" with two drafts → the first draft was outside the 8-entry window.
   Fix: 12 entries, limited to 8 hours (so a draft deferred days ago does not count).

Also: the guard narrows ambiguous candidates by what the action can apply to; the prompt
tells the IA to ask naturally (it once said "the system wants you to confirm"); grading
counts a correct answer from the card that ends with an offer as correct (all setups).

Harness fixes found during r1 (affect all setups): pooled HTTP client (parallel calls were
serialized by per-call SSL setup); "#15 Name" now resolves to agent 15 (models append the
name; without this our router created an agent literally named "#15 Air Canada Refund").

## dev-haiku-r2 (after the r1 fixes; same model, scenarios and sizes)

Cost $2.89. Results: `evals/results/dev-haiku-r2/summary.md`.

| Setup | 30 | 200 | 1,000 | Asked when ambiguous | Wrong reuse | Prompt tokens at 1,000 |
|---|---|---|---|---|---|---|
| B0 OpenPoke today | 96% | 95% | 91% | 75% | 1% | 51k |
| B1 every card | 92% | 94% | 92% | 62–75% | 0–3% | 115k |
| B2 common answer | 92% | 96% | 92% | 75% | 0–4% | 14k |
| Ours | 99% | 97% | 95% | 100% | 0% | 19k |

Tuned on these scenarios, so this is an upper bound; held-out decides.

Remaining Ours misses at 1,000 and what was done:
- "forward the Globex offer letter" to an archived agent was challenged by the
  send-to-agent-without-draft rule; that rule now applies only to *sending a draft*.
  (General bug; regression test added.)
- "that japan food list" vs agent "Tokyo Ramen Search": no shared words; our search is
  lexical. Known limit (B2's embeddings help here). Next step: optional embeddings.
- "the alice thing" asked again after the user already chose: the corrections-memory
  stretch (aliases) is not built.
- "remind me about the hydro bill" → asked for the due date: a task-detail question the
  grader counts as over-asking for every setup alike. Grading limitation, noted.

Held-out set frozen by hash (`heldout.sha256`, 4249db74…) before any held-out run.

## heldout-haiku (stopped deliberately at 5,000 agents)

Held-out, 31 scenarios × 2 reps, sizes 30/200/1,000 complete for B0, B2, Ours; B0 at
5,000 has 51 of 62 turns. Stopped because B0's cost exploded: at 5,000 agents a turn is
227k prompt tokens and **$0.115** (OpenRouter's reported cost: about 5× Haiku's list price
per token at that prompt length), versus **$0.002** for Ours. Projected B0 cost at 20,000
agents was ~$28 for this run alone, so B0 at 20,000 is run on a small sample instead, and
B2/Ours at 5,000 and 20,000 run separately (`heldout-haiku-scale`).

Latency (p50 per turn): B0 3.2 s → 3.6 s → 3.6 s → 5.3 s from 30 to 5,000 agents (p95 18 s
at 1,000); Ours 2.9–3.1 s at every size.

Held-out misses for Ours (reported, not tuned on): 4 of 7 are family 10 (repeat after a
clarification: the model even wrote "the one you picked earlier" and still asked; this is
the corrections-memory feature that is not built); 2 are a needle the lexical search missed
(new agent instead of the old airline one); 1 is a multi-dispatch turn the grader scores by
its last dispatch.

## Headline queue 1 (13:00–13:17)

| Run | Model | Result | Cost |
|---|---|---|---|
| heldout-haiku-20k | Haiku | B2, Ours at 20,000 agents, 2 reps (Ours on pre-guard-fix code; superseded by `heldout-haiku-ours-final`) | ~$0.4 |
| heldout-sonnet | Sonnet 5.5 | 30 and 1,000 complete for B0/B2/Ours; stopped by its $20 cap at 5,000 after 9 B0 turns | $21.87 |
| heldout-b0-20k | Haiku | B0 at 20,000: cap hit after 10 turns, **6/10 correct (3 duplicates, 1 wrong reuse)**, $0.44/turn | $4.86 |
| heldout-b1 | Haiku | B1 at 30/200/1,000: 97% / 94% / 94% | $0.53 |
| heldout-ablation | Haiku | Ours at 1,000 without resolver 98%, without contract 94%, without related cards 90% (4 duplicates) | $0.39 |
| heldout-gpt | GPT-6.1-terra (OpenAI) | B0 90%/90%, B2 81%/77%, Ours 90%/87% at 30/1,000; asked-when-ambiguous 33% / 0–33% / 67% | $3.06 (OpenAI) |

Caps overshoot slightly (in-flight calls finish after the cap check): Sonnet ended at $21.87
on a $20 cap. B0's cost at large rosters dominates: $0.18/turn at 1,000 and $0.68/turn at
5,000 on Sonnet, versus ~$0.05 for Ours at every size.

Sonnet 5.5 is strong enough that B0 holds 97% at 1,000 agents on these held-out scenarios;
Ours is 94% / 90%. Read honestly: at ≤1,000 agents with a strong model, Ours is a cost and
safety improvement, not an accuracy one. The ablation finding (resolver off scored higher)
is checked against Ours on final code in queue 2 before drawing conclusions.
