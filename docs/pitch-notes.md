# Pitch notes (≈5 minutes + walkthrough)

## 1. Framing (1 min) — "the list is a symptom"

- OpenPoke shows the Interaction Agent every agent ever created, by bare name, and decides
  spawn-vs-reuse by exact string match.
- I surveyed the public forks first (`docs/research/2026-11-20-fork-survey.md`): ~20 full
  solutions converge on the same answer: richer cards + retrieval + search + duplicate guard.
  Their own write-ups show where it stops: the model rarely asks when two things match (2 of
  30 in one), reuses agents across people, and still degrades with scale.
- Those residual errors are **reference errors**: which draft does "send it" mean, which Alice,
  old thread or new work. A model guessing from better cards is the wrong tool.
- **Thesis:** resolve references in code where the data allows; make "ambiguous, ask" an
  outcome enforced in code; show **open items**, not history. Little's Law bounds open items by
  how busy the user is, not how long they have used it.

## 2. What I built (1 min)

Registry with stable ids and open-loop states derived from tool calls → bounded working set
(≤31 lines at any size) → resolver (thread/draft ids, "send it", email replies, named people)
→ ambiguity contract in `send_message_to_agent` (ask instead of guessing on irreversible steps;
challenge wrong-target sends; route owned work; flag duplicates) → learns the user's phrase as
an alias after a clarification. `OPENPOKE_ROUTER=legacy` = upstream byte-for-byte.

## 3. Evidence (1.5 min) — show `evals/results/heldout-haiku/accuracy_vs_size.png`

- Held-out set frozen by hash before any held-out run; 4 setups incl. an honest build of the
  common fork answer; 3 model families.
- **Same accuracy as today up to 1,000 agents; +10 points at 5,000, +40 at 20,000** (today's
  design: 60%, $0.44/turn; ours ~95%, $0.002).
- **Asks 100% of the time when truly ambiguous** (others 33–83%), **zero wrong reuse**, 4% over-ask.
- Honest read: with strong models at ≤1,000 agents everyone is accurate; the value there is
  safety and flat cost. Ablation shows exactly where the resolver costs accuracy (family 10)
  and why the alias memory is the fix.

## 4. Beyond the brief (30 s)

Per-agent run serialization (double-sends), bounded execution-agent memory, `max_tokens`,
Windows, retired default model (`claude-sonnet-4` no longer served: upstream is broken out of
the box), email replies wake the thread owner, upstream lint error, and **the original
author's OpenRouter key is in the repo history** (commit `b5695fa`, every fork inherits it).

## 5. Live demo script (real server, Sonnet 5.5)

1. "draft an email to me@… about the Q3 deck" → then "and one about lunch thursday"
2. "send it" → Poke asks which one (needs_user_choice), in its own voice.
3. "the lunch one" → sends that one; the phrase is learned.
4. "remind me to call mom tomorrow at 6" → "push it to 7" → same agent (#id reuse), note updated.
5. Show `server/data/execution_agents/agents.db` working set (or the `<agents>` block in logs).

## Likely questions

- **Why not just embeddings?** B2 *is* embeddings + BM25 + cards; it ties on accuracy but
  reuses the wrong agent up to 7% and asks on ambiguity only 40–100%. Embeddings find
  candidates; they cannot tell which of two drafts "send it" means.
- **Isn't the resolver brittle (regexes)?** It only settles what is unambiguous from data and
  says "ambiguous" otherwise; the model still decides everything unresolved. Every rule has
  tests (48), and the ablation shows its one cost.
- **Does it scale to many users?** Single-user like upstream; per-tenant registry + FTS shards
  cleanly (Postgres), working set is O(open items). Next steps in the write-up.
- **Synthetic data?** Yes; mitigations: separate held-out seed and wording frozen by hash, a
  strong common-answer baseline, 3 model families, other forks' own held-out tests (external
  run), and an owner-run real-Poke spot check sheet.
- **Over-asking?** 4%, mostly family 10 (alias memory fixes it live) and task-detail questions
  the grader counts conservatively.
