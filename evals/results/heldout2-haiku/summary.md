# Agent-overload eval results

## Model: `anthropic/claude-haiku-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 31 | 97% [90–100] | 0% | 0% | 0% | 67% | 0% | 13,480 | 3.7 | 0.0016 |
| 30 | Common fork answer | 31 | 97% [90–100] | 0% | 4% | 33% | 100% | 0% | 14,757 | 3.2 | 0.0017 |
| 30 | Ours (reference-first) | 31 | 100% [100–100] | 0% | 0% | 0% | 100% | 0% | 17,357 | 2.6 | 0.0019 |
| 1,000 | OpenPoke today | 31 | 100% [100–100] | 0% | 0% | 0% | 100% | 0% | 45,403 | 3.8 | 0.0048 |
| 1,000 | Common fork answer | 31 | 90% [77–100] | 4% | 0% | 0% | 67% | 4% | 15,505 | 3.6 | 0.0018 |
| 1,000 | Ours (reference-first) | 31 | 94% [84–100] | 4% | 0% | 0% | 100% | 4% | 18,861 | 2.8 | 0.0021 |
| 5,000 | Common fork answer | 31 | 90% [77–100] | 0% | 0% | 0% | 33% | 4% | 15,705 | 3.3 | 0.0018 |
| 5,000 | Ours (reference-first) | 31 | 94% [84–100] | 4% | 0% | 0% | 100% | 4% | 19,610 | 3.3 | 0.0022 |
| 20,000 | Common fork answer | 31 | 87% [74–97] | 4% | 4% | 33% | 67% | 4% | 16,268 | 6.8 | 0.0019 |
| 20,000 | Ours (reference-first) | 31 | 100% [100–100] | 0% | 0% | 0% | 100% | 0% | 21,170 | 3.5 | 0.0024 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | +3 pts [+0, +10] | 31 |
| 30 | OpenPoke today | +3 pts [+0, +10] | 31 |
| 1,000 | Common fork answer | +3 pts [+0, +10] | 31 |
| 1,000 | OpenPoke today | -6 pts [-16, +0] | 31 |
| 5,000 | Common fork answer | +3 pts [-6, +13] | 31 |
| 20,000 | Common fork answer | +13 pts [+3, +26] | 31 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 1,000 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Common fork answer | 30 | 100% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% |
| Common fork answer | 1,000 | 67% | 100% | 100% | 67% | 100% | 67% | 100% | 100% | 100% | 100% |
| Common fork answer | 5,000 | 100% | 100% | 100% | 33% | 100% | 67% | 100% | 100% | 100% | 100% |
| Common fork answer | 20,000 | 100% | 67% | 100% | 67% | 100% | 33% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 30 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 1,000 | 67% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 5,000 | 67% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 20,000 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |

