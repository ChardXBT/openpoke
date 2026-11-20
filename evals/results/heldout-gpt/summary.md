# Agent-overload eval results

## Model: `openai:gpt-6.1-terra`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 31 | 90% [81–100] | 0% | 4% | 33% | 33% | 0% | 9,661 | 4.9 | 0.0101 |
| 30 | Common fork answer | 31 | 81% [65–94] | 0% | 7% | 67% | 0% | 0% | 10,927 | 4.9 | 0.0113 |
| 30 | Ours (reference-first) | 31 | 90% [77–100] | 0% | 0% | 0% | 67% | 4% | 12,167 | 4.7 | 0.0125 |
| 1,000 | OpenPoke today | 31 | 90% [77–100] | 4% | 0% | 0% | 33% | 0% | 39,616 | 5.7 | 0.0401 |
| 1,000 | Common fork answer | 31 | 77% [61–90] | 0% | 7% | 67% | 33% | 4% | 10,519 | 4.3 | 0.0109 |
| 1,000 | Ours (reference-first) | 31 | 87% [74–97] | 4% | 0% | 0% | 67% | 4% | 13,282 | 4.6 | 0.0137 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | +10 pts [-3, +23] | 31 |
| 30 | OpenPoke today | +0 pts [-13, +13] | 31 |
| 1,000 | Common fork answer | +10 pts [-6, +26] | 31 |
| 1,000 | OpenPoke today | -3 pts [-16, +10] | 31 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 100% | 100% | 100% | 33% | 100% | 67% | 100% | 100% | 100% | 100% |
| OpenPoke today | 1,000 | 100% | 67% | 100% | 33% | 100% | 100% | 100% | 100% | 100% | 100% |
| Common fork answer | 30 | 100% | 100% | 100% | 0% | 100% | 33% | 100% | 100% | 67% | 100% |
| Common fork answer | 1,000 | 100% | 100% | 100% | 33% | 67% | 33% | 100% | 100% | 33% | 100% |
| Ours (reference-first) | 30 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 67% | 67% |
| Ours (reference-first) | 1,000 | 67% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 67% | 67% |

