# Agent-overload eval results

## Model: `anthropic/claude-haiku-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 78 | 96% [91–100] | 0% | 1% | 0% | 75% | 0% | 13,196 | 2.9 | 0.0015 |
| 30 | Full list + descriptions | 78 | 92% [86–97] | 0% | 1% | 0% | 62% | 1% | 15,559 | 3.2 | 0.0018 |
| 30 | Common fork answer | 78 | 92% [86–97] | 0% | 4% | 25% | 75% | 1% | 14,512 | 2.8 | 0.0017 |
| 30 | Ours (reference-first) | 78 | 99% [96–100] | 0% | 0% | 0% | 100% | 1% | 17,521 | 2.9 | 0.0019 |
| 200 | OpenPoke today | 78 | 95% [90–99] | 2% | 1% | 0% | 75% | 0% | 18,274 | 3.2 | 0.0021 |
| 200 | Full list + descriptions | 78 | 94% [88–99] | 0% | 0% | 0% | 75% | 1% | 32,705 | 3.1 | 0.0035 |
| 200 | Common fork answer | 78 | 96% [91–100] | 0% | 0% | 0% | 75% | 1% | 14,819 | 3.2 | 0.0017 |
| 200 | Ours (reference-first) | 78 | 97% [94–100] | 2% | 0% | 0% | 100% | 1% | 18,921 | 2.8 | 0.0021 |
| 1,000 | OpenPoke today | 78 | 91% [85–96] | 6% | 1% | 0% | 75% | 0% | 51,009 | 3.4 | 0.0053 |
| 1,000 | Full list + descriptions | 78 | 92% [86–97] | 0% | 3% | 12% | 75% | 1% | 114,880 | 3.7 | 0.0117 |
| 1,000 | Common fork answer | 78 | 92% [86–97] | 0% | 4% | 25% | 75% | 1% | 14,402 | 5.7 | 0.0017 |
| 1,000 | Ours (reference-first) | 78 | 95% [90–99] | 3% | 0% | 0% | 100% | 3% | 18,842 | 2.9 | 0.0021 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | +6 pts [+0, +13] | 78 |
| 30 | Full list + descriptions | +6 pts [+0, +13] | 78 |
| 30 | OpenPoke today | +3 pts [-1, +8] | 78 |
| 200 | Common fork answer | +1 pts [-4, +6] | 78 |
| 200 | Full list + descriptions | +4 pts [-3, +10] | 78 |
| 200 | OpenPoke today | +3 pts [-4, +9] | 78 |
| 1,000 | Common fork answer | +3 pts [-5, +9] | 78 |
| 1,000 | Full list + descriptions | +3 pts [-5, +10] | 78 |
| 1,000 | OpenPoke today | +4 pts [-4, +12] | 78 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 100% | 100% | 100% | 75% | 100% | 100% | 88% | 100% | 100% | 100% |
| OpenPoke today | 200 | 88% | 88% | 100% | 75% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 1,000 | 62% | 100% | 100% | 75% | 100% | 100% | 88% | 100% | 88% | 100% |
| Full list + descriptions | 30 | 100% | 75% | 100% | 62% | 100% | 100% | 88% | 100% | 100% | 100% |
| Full list + descriptions | 200 | 100% | 75% | 90% | 75% | 100% | 100% | 100% | 100% | 100% | 100% |
| Full list + descriptions | 1,000 | 100% | 75% | 100% | 75% | 100% | 88% | 88% | 100% | 100% | 100% |
| Common fork answer | 30 | 100% | 88% | 100% | 75% | 100% | 75% | 88% | 100% | 100% | 100% |
| Common fork answer | 200 | 100% | 100% | 100% | 75% | 100% | 88% | 100% | 100% | 100% | 100% |
| Common fork answer | 1,000 | 100% | 100% | 100% | 75% | 100% | 62% | 88% | 100% | 100% | 100% |
| Ours (reference-first) | 30 | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 75% |
| Ours (reference-first) | 200 | 88% | 100% | 100% | 100% | 100% | 88% | 100% | 100% | 100% | 100% |
| Ours (reference-first) | 1,000 | 88% | 100% | 100% | 100% | 100% | 88% | 100% | 100% | 88% | 75% |

