# Agent-overload eval results

## Model: `anthropic/claude-sonnet-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 31 | 97% [90–100] | 0% | 0% | 0% | 67% | 0% | 21,048 | 5.2 | 0.0463 |
| 30 | Common fork answer | 31 | 94% [84–100] | 0% | 0% | 0% | 67% | 0% | 21,708 | 4.8 | 0.0473 |
| 30 | Ours (reference-first) | 31 | 94% [84–100] | 0% | 4% | 33% | 100% | 4% | 25,075 | 4.6 | 0.0536 |
| 1,000 | OpenPoke today | 31 | 97% [90–100] | 0% | 0% | 0% | 67% | 0% | 87,124 | 5.7 | 0.1787 |
| 1,000 | Common fork answer | 31 | 94% [84–100] | 0% | 0% | 0% | 33% | 0% | 23,056 | 4.9 | 0.0502 |
| 1,000 | Ours (reference-first) | 31 | 90% [81–100] | 4% | 4% | 33% | 100% | 4% | 24,806 | 4.2 | 0.0530 |
| 5,000 | OpenPoke today | 9 | 100% [100–100] | 0% | 0% | – | 100% | 0% | 339,984 | 6.4 | 0.6835 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | +0 pts [-13, +13] | 31 |
| 30 | OpenPoke today | -3 pts [-13, +10] | 31 |
| 1,000 | Common fork answer | -3 pts [-19, +10] | 31 |
| 1,000 | OpenPoke today | -6 pts [-19, +6] | 31 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 1,000 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 5,000 | 100% | 100% | 100% | 100% | – | – | – | – | – | – |
| Common fork answer | 30 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 67% | 100% |
| Common fork answer | 1,000 | 100% | 100% | 100% | 33% | 100% | 100% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 30 | 100% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 67% |
| Ours (reference-first) | 1,000 | 67% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 67% |

