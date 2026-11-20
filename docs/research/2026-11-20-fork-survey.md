# Survey of public OpenPoke forks (2026-11-20)

Why: OpenPoke has 157 forks and its own repo has been quiet since 2025-09. Before
building, we checked what others built on top of it, so our work adds something new
instead of repeating the common answer. Method: GitHub API, every fork compared with
upstream `main`; READMEs and write-ups read for the forks that changed routing. Nothing
was run, and no code or data was taken from any fork.

## What we found

- 62 forks had pushes after forking; 45 are ahead of upstream.
- At least 20 are full solutions to the agent-overload problem, mostly pushed
  Oct–Nov 2026, several with hundreds of commits, 300–900 offline tests and
  pre-registered evaluation targets.
- Upstream PRs repeat the same ideas: #15 semantic shortlisting, #11 smart roster,
  #12 bounded agent context, #13 typed decisions with Jev (TypeSafe's calibrated
  classifier), #14 memory privacy analysis.

## The common solution

Nearly every serious fork converges on the same stack:

| Piece | Seen in |
|---|---|
| Agent records/cards (ID, purpose, recent work, status) instead of bare names | almost all |
| Retrieval: BM25 and/or embeddings, top 5–18 cards plus a recent-agent cache | almost all |
| A search tool for agents not shown | many |
| Reuse by ID; duplicate guard on spawn | many |
| Bounded execution-agent history / compaction | many |
| Code-level send-approval gate | samintisar, sina158, draggle, amazadfar |
| Prompt-injection suite | sina158, 0xShin0221, draggle |
| Lifecycle simulation (30–60 days) | sina158, draggle |
| Fake Gmail backend for end-to-end runs | talfigh, samintisar, amazadfar |
| Held-out sets, bootstrap CIs, paired tests, pre-registered targets | samintisar, sina158, draggle, KareemElMaaddawy, RayanHossain |
| Concurrency lock, per-turn batching, Windows `fcntl` | samintisar, amazadfar |

Typical headline results: 0.86–0.93 routing accuracy at 150–1,000 agents, from
baselines of 0.46–0.82; duplicates down to 0–4%.

## Where it stops working (their own reported failures)

These are the open problems, taken from the forks' own write-ups:

1. **The model rarely asks when two things match.** sina158: the model asked the user
   on 2 of 30 ambiguous cases. samintisar: harmful (wrong-agent) rate 0.049 against a
   0.01 target at 1,000 agents.
2. **Reuse across people and topics.** sina158: 11 of 252 cross-client wrong reuses; 19
   of 312 turns sent to two clients. amazadfar: "the model still re-uses existing
   agents for unrelated threads" (merge decisions 40 → 20, gate failed).
3. **The duplicate vs wrong-reuse trade-off.** KareemElMaaddawy: fixing wrong reuse
   pushed duplicates to 15% of cases an existing agent owned.
4. **Accuracy still falls as the roster grows, even with retrieval.** draggle: 92% at
   50 agents, 80% at 500, 74% at 1,000.
5. **Tokens are not always lower.** sina158: 103% of baseline tokens per turn.
6. **Agents created from email alerts alone** (talfigh, open); unrequested sends when
   only a draft was asked for (amazadfar).
7. **Evaluation validity.** All synthetic; usually one model; labels revised after
   seeing results; most scale tests stop at 1,000 agents.

## What this means for us

The common answer gives the model better cards and lets it guess. Its residual errors
are all reference errors: which draft does "send it" mean, which Alice, is this new
work or the old thread. Those are exactly the cases where a guess is most costly and
where the model, by the forks' own numbers, does not ask.

So our differentiator is to resolve references in code where the data allows it
(thread, draft and contact IDs, explicit `#ids`, the open items just shown to the user),
make "ambiguous, ask the user" an outcome enforced in code rather than requested in
the prompt, and leave the model to choose only among what code could not settle. We
measure against the failure classes above, at sizes up to 20,000 agents.

Table stakes we still need (because every strong fork has them): stable IDs, cards,
search, bounded working set, held-out evaluation with intervals, baselines on the
upstream prompt.

## Forks referenced

samintisar/openpoke, sina158/openpoke (`gm-takehome`), draggle/openpoke,
KareemElMaaddawy/openpoke-enhanced, RayanHossain/openpoke, talfigh/openpoke
(`agent-overload`), amazadfar/openpoke-agent-overload, n26modi/openpoke-jev-routing,
0xShin0221/openpoke-meets-jev, ryanwoong/openpoke, Ali-AlRawaf/openpoke,
WilliamZhao1/openpoke (graph retrieval), ammar-adam/openpoke, Dannny-Babs/openpoke,
xchkcode0803/openpoke, cherylaurelia/openpoke, manav-vc/openpoke, JustHarsh/openpoke.
