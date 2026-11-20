# Agent-overload eval results

## Model: `anthropic/claude-haiku-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 62 | 97% [92–100] | 0% | 0% | 0% | 67% | 0% | 12,496 | 3.2 | 0.0015 |
| 30 | Full list + descriptions | 31 | 97% [90–100] | 0% | 0% | 0% | 67% | 0% | 16,829 | 3.9 | 0.0019 |
| 30 | Common fork answer | 62 | 98% [95–100] | 0% | 0% | 0% | 100% | 0% | 13,976 | 3.0 | 0.0016 |
| 30 | Ours (reference-first) | 62 | 97% [92–100] | 0% | 0% | 0% | 100% | 4% | 17,012 | 2.9 | 0.0019 |
| 200 | OpenPoke today | 62 | 97% [92–100] | 2% | 0% | 0% | 83% | 0% | 18,879 | 3.6 | 0.0021 |
| 200 | Full list + descriptions | 31 | 94% [84–100] | 0% | 0% | 0% | 33% | 0% | 35,759 | 3.8 | 0.0038 |
| 200 | Common fork answer | 62 | 92% [85–98] | 0% | 7% | 33% | 83% | 0% | 14,597 | 3.0 | 0.0017 |
| 200 | Ours (reference-first) | 62 | 97% [92–100] | 0% | 0% | 0% | 100% | 4% | 17,235 | 2.8 | 0.0019 |
| 1,000 | OpenPoke today | 62 | 94% [87–98] | 4% | 0% | 0% | 67% | 0% | 54,554 | 3.6 | 0.0057 |
| 1,000 | Full list + descriptions | 31 | 94% [84–100] | 0% | 0% | 0% | 67% | 0% | 112,332 | 3.8 | 0.0115 |
| 1,000 | Common fork answer | 61 | 89% [80–95] | 0% | 7% | 33% | 40% | 0% | 14,964 | 3.0 | 0.0017 |
| 1,000 | Ours (reference-first) | 62 | 95% [89–100] | 2% | 0% | 0% | 100% | 4% | 17,902 | 2.8 | 0.0020 |
| 5,000 | OpenPoke today | 51 | 88% [78–96] | 10% | 0% | 0% | 67% | 0% | 227,115 | 5.3 | 0.1149 |
| 5,000 | Common fork answer | 62 | 94% [87–98] | 0% | 2% | 0% | 50% | 0% | 15,331 | 3.8 | 0.0018 |
| 5,000 | Ours (reference-first) | 62 | 95% [90–100] | 2% | 0% | 0% | 100% | 4% | 17,890 | 2.8 | 0.0020 |
| 20,000 | OpenPoke today | 10 | 60% [30–90] | 33% | 11% | – | 100% | 0% | 880,163 | 12.2 | 0.4415 |
| 20,000 | Common fork answer | 62 | 97% [92–100] | 0% | 0% | 0% | 67% | 0% | 15,576 | 3.8 | 0.0018 |
| 20,000 | Ours (reference-first) | 62 | 95% [90–100] | 2% | 0% | 0% | 100% | 4% | 17,760 | 3.1 | 0.0020 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | -2 pts [-8, +5] | 62 |
| 30 | Full list + descriptions | +0 pts [-10, +10] | 31 |
| 30 | OpenPoke today | +0 pts [-6, +6] | 62 |
| 200 | Common fork answer | +5 pts [-3, +13] | 62 |
| 200 | Full list + descriptions | +3 pts [-6, +16] | 31 |
| 200 | OpenPoke today | +0 pts [-6, +6] | 62 |
| 1,000 | Common fork answer | +7 pts [-3, +16] | 61 |
| 1,000 | Full list + descriptions | +3 pts [-6, +16] | 31 |
| 1,000 | OpenPoke today | +2 pts [-8, +10] | 62 |
| 5,000 | Common fork answer | +2 pts [-6, +10] | 62 |
| 5,000 | OpenPoke today | +10 pts [+2, +20] | 51 |
| 20,000 | Common fork answer | -2 pts [-8, +5] | 62 |
| 20,000 | OpenPoke today | +40 pts [+10, +70] | 10 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 200 | 83% | 100% | 100% | 83% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 1,000 | 67% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 5,000 | 33% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | – |
| OpenPoke today | 20,000 | 33% | 33% | 100% | 100% | – | – | – | – | – | – |
| Full list + descriptions | 30 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| Full list + descriptions | 200 | 100% | 100% | 100% | 33% | 100% | 100% | 100% | 100% | 100% | 100% |
| Full list + descriptions | 1,000 | 100% | 100% | 75% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| Common fork answer | 30 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 83% | 100% |
| Common fork answer | 200 | 100% | 100% | 100% | 83% | 100% | 67% | 100% | 100% | 100% | 67% |
| Common fork answer | 1,000 | 100% | 100% | 100% | 40% | 100% | 67% | 67% | 100% | 100% | 100% |
| Common fork answer | 5,000 | 83% | 100% | 100% | 50% | 100% | 100% | 100% | 100% | 100% | 100% |
| Common fork answer | 20,000 | 100% | 100% | 100% | 67% | 100% | 100% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 30 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% |
| Ours (reference-first) | 200 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% |
| Ours (reference-first) | 1,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% |
| Ours (reference-first) | 5,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% |
| Ours (reference-first) | 20,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% |

