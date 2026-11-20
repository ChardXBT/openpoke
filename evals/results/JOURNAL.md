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

## Final queue 4 (13:47–14:08), code 78031cd → b26694c (post-tuning)

Held-out runs here are **post-tuning** (review finding 14): the seed-2 misses were read at
12:52 and the code changed afterwards. They are reported as such, never as the headline.
During the queue, the commit changed to `4355a88` (demo mailbox) and then `b26694c` (memory).
Neither touches anything the eval setups use: the setups run with an empty, isolated profile.

| Run | Model | Result | Cost |
|---|---|---|---|
| external-haiku-final | Haiku | Kareem 105: B0 88%, B2 81%, Ours **94%**. draggle 72: B0 74%, B2 67%, Ours 67% | $1.07 |
| dev-haiku-final | Haiku | 1,000 agents: B2 90%, Ours **97%** (ask recall 75% vs 100%) | $0.29 |
| heldout-haiku-post | Haiku | 1,000 agents: B2 90%, Ours **100%** | $0.12 |
| external-gpt-final | GPT-6.1 | Kareem: B0 86%, B2 91%, Ours **98%**. draggle: B0 72%, B2 79%, Ours 75% | $7.48 |
| heldout-gpt-post | GPT-6.1 | 30 / 1,000 agents: B0 87% / 94%, B2 71% / 81%, Ours **97%** / 90% | $3.12 |
| heldout-gpt-post-5k | GPT-6.1 | 5,000 agents: B2 77%, Ours **94%** | $0.80 |
| heldout-gpt-b0-20k | GPT-6.1 | B0 at 20,000, `--sample 12` (evenly spaced, every family): cap after 5 turns, 5/5 correct, **$0.79/turn, 789k prompt tokens/turn** | $5.53 |
| dev-gpt-final | GPT-6.1 | 30 agents: B0 91%, B2 88%, Ours **96%**. At 1,000 agents only B0 finished (87%, n=30) before the $4 cap | $4.04 |

draggle, read closely: its gold expects a dispatch on every query. 10 (Haiku) and 4 (GPT)
of Ours' misses, and 13 / 7 of B2's, are replies given straight from the agent's card with
content that matches the gold agent's notes. Crediting those: B0 74% / 72%, B2 **85% / 89%**,
Ours 81% / 81%. On draggle's 50-agent test, the common fork answer is the best design.

## Personal memory eval (14:08–14:20)

`evals/memory/`: one extraction per case through the product's `build_extraction_messages` and
`apply_extraction` with an in-memory store, then the profile is checked. v1 is the learner prompt
before this work, frozen verbatim in the eval (checked against `git show`). 3 reps per case.

- **memory-1** (51 dev cases, v2 as first written): v1 76–78% → v2 92–98%; false writes 0%. $0.19.
- Misses read, then fixed (this makes the 51 cases *dev*):
  - Luna ignored "yes please" to Poke's own "should I remember…?".
  - Luna dropped a lasting fact inside a request ("call my dad Mike").
  - Models copied the display form "gym (routine)" as a subject.
- Grader changes, both after reading run 1:
  - `gone` checks only the starting subjects. All three models correctly retired the job and
    added "time off after leaving Shopify", which the old check failed.
  - c2 no longer requires saving an address that appeared only in Poke's message. The address
    came from an email, and the models were right to refuse it: a spoofed sender must not
    become "my dentist".
- **memory-2-dev**: v2 99–100% (v1 76–77%). $0.21.
- **memory-heldout**: 20 new cases written after that tuning, frozen by sha256 in `3ff6ec7`
  before the run, and run once: **v1 63–68% → v2 97–100%**, false writes 7% → 0% (Luna). $0.09.

## heldout2: the fresh split, run once (14:22–14:37), code 7b6ecfa5 (freeze commit 29d59d2)

Written after all tuning. Frozen with its scenario digest and a routing-code digest before any
run, and the runner logged each cell to `heldout2.runs.jsonl`.

| Run | Model | Result | Cost |
|---|---|---|---|
| heldout2-haiku-small | Haiku | 30 / 1,000 agents: B0 97% / **100%**, B2 97% / 90%, Ours **100%** / 94% | $0.43 |
| heldout2-haiku-large | Haiku | 5,000 / 20,000 agents: B2 90% / 87%, Ours **94% / 100%** (paired +13 [+3, +26] at 20,000) | $0.26 |
| heldout2-gpt-small | GPT-6.1 | 30 / 1,000 agents: B0 87% / 81%, B2 90% / 84%, Ours **100% / 90%** | $3.19 |
| heldout2-gpt-large | GPT-6.1 | 5,000 agents: B2 84%, Ours **90%**. The 20,000 cell never started: the process was killed by a session restart at 14:37. Its summary was rebuilt from `rows.jsonl` | $1.68 |

Ours' Haiku misses at 1,000 agents: a paraphrase the lexical search missed ("paying twice" vs
*Subscription Audit*), and an over-ask on the day of a monthly reminder. Not tuned on.

## Live demo and post-run fixes (14:40–15:20), commits 9a81406, 40c54d7, cc2a15b

The demo ran end to end through the API and then the web UI, with the demo mailbox on GPT-6.1,
for about $1.5 in total. It found:

1. **"ask alice if thursday lunch works" reused Alice's Q3-deck agent.** The entity rule returned
   the person's only open item without checking the topic. Fixed: no claim when the topic words
   fit none of the person's open items. Action and status words are ignored ("confirm" had matched
   "Conf"; "note" tied two drafts and made "send the lunch note" ask).
2. **A reply never closed the item that was waiting on it.** Fixed via the thread id.
3. **The demo email button depended on the importance model**, which once judged a lunch reply
   not important. The demo now hands the email to Poke directly; the real watcher is unchanged.

Offline resolver check (no model; the resolver against gold on all 140 scenarios at 30, 1,000 and
20,000 agents): wrong calls dev 4 → 0 and heldout2 2 → 0; right calls dev 53 → 54, heldout 22 → 22,
heldout2 19 → 20. This uses heldout2, so heldout2 is no longer clean for future resolver changes.
