# Agent-overload eval results

## Model: `openai:gpt-6.1-terra`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 31 | 87% [74–97] | 0% | 4% | 0% | 0% | 0% | 9,977 | 5.1 | 0.0105 |
| 30 | Common fork answer | 31 | 90% [77–100] | 0% | 0% | 0% | 33% | 4% | 10,843 | 4.8 | 0.0112 |
| 30 | Ours (reference-first) | 31 | 100% [100–100] | 0% | 0% | 0% | 100% | 0% | 14,001 | 4.5 | 0.0144 |
| 1,000 | OpenPoke today | 31 | 81% [68–94] | 0% | 7% | 0% | 0% | 4% | 39,345 | 5.7 | 0.0398 |
| 1,000 | Common fork answer | 31 | 84% [71–97] | 0% | 4% | 33% | 0% | 4% | 11,524 | 4.9 | 0.0120 |
| 1,000 | Ours (reference-first) | 31 | 90% [81–100] | 4% | 0% | 0% | 100% | 4% | 14,530 | 4.6 | 0.0149 |
| 5,000 | Common fork answer | 31 | 84% [71–97] | 0% | 4% | 33% | 33% | 7% | 10,701 | 4.3 | 0.0111 |
| 5,000 | Ours (reference-first) | 31 | 90% [77–100] | 4% | 0% | 0% | 67% | 4% | 15,376 | 4.6 | 0.0158 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | +10 pts [+0, +23] | 31 |
| 30 | OpenPoke today | +13 pts [+3, +26] | 31 |
| 1,000 | Common fork answer | +6 pts [-10, +23] | 31 |
| 1,000 | OpenPoke today | +10 pts [-6, +26] | 31 |
| 5,000 | Common fork answer | +6 pts [-10, +23] | 31 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 100% | 67% | 100% | 0% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 1,000 | 100% | 33% | 100% | 0% | 100% | 67% | 100% | 100% | 100% | 100% |
| Common fork answer | 30 | 100% | 100% | 100% | 33% | 100% | 67% | 100% | 100% | 100% | 100% |
| Common fork answer | 1,000 | 100% | 100% | 100% | 0% | 100% | 33% | 100% | 100% | 100% | 100% |
| Common fork answer | 5,000 | 100% | 100% | 75% | 33% | 100% | 33% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 30 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 1,000 | 67% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 67% | 100% |
| Ours (reference-first) | 5,000 | 67% | 100% | 100% | 67% | 100% | 67% | 100% | 100% | 100% | 100% |

