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
