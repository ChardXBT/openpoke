# Agent-overload eval results

## Model: `anthropic/claude-haiku-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 | OpenPoke today | 78 | 95% [90–99] | 2% | 0% | 0% | 62% | 0% | 12,893 | 3.0 | 0.0015 |
| 30 | Full list + descriptions | 78 | 94% [87–99] | 0% | 0% | 0% | 75% | 3% | 15,256 | 3.0 | 0.0017 |
| 30 | Common fork answer | 78 | 94% [87–99] | 0% | 3% | 25% | 75% | 1% | 14,843 | 3.1 | 0.0017 |
| 30 | Ours (reference-first) | 78 | 95% [90–99] | 0% | 0% | 0% | 88% | 4% | 17,052 | 2.9 | 0.0019 |
| 200 | OpenPoke today | 78 | 90% [82–96] | 5% | 4% | 0% | 75% | 0% | 19,111 | 3.2 | 0.0021 |
| 200 | Full list + descriptions | 78 | 94% [87–99] | 0% | 0% | 0% | 88% | 3% | 29,636 | 3.1 | 0.0032 |
| 200 | Common fork answer | 78 | 94% [88–99] | 0% | 1% | 0% | 75% | 3% | 15,029 | 3.5 | 0.0017 |
| 200 | Ours (reference-first) | 78 | 94% [88–99] | 0% | 0% | 0% | 100% | 7% | 18,957 | 2.9 | 0.0021 |
| 1,000 | OpenPoke today | 78 | 90% [82–96] | 8% | 1% | 0% | 75% | 0% | 50,334 | 3.4 | 0.0053 |
| 1,000 | Full list + descriptions | 78 | 94% [87–99] | 0% | 1% | 12% | 75% | 0% | 116,319 | 3.7 | 0.0119 |
| 1,000 | Common fork answer | 78 | 91% [85–96] | 0% | 4% | 25% | 75% | 3% | 14,227 | 8.5 | 0.0016 |
| 1,000 | Ours (reference-first) | 78 | 94% [88–99] | 3% | 0% | 0% | 100% | 3% | 18,898 | 3.0 | 0.0021 |

### Paired difference in correct rate (Ours minus baseline, same scenarios)

| Agents | Baseline | Δ correct [95% CI] | pairs |
|---:|---|---|---:|
| 30 | Common fork answer | +1 pts [-5, +8] | 78 |
| 30 | Full list + descriptions | +1 pts [-5, +8] | 78 |
| 30 | OpenPoke today | +0 pts [-8, +6] | 78 |
| 200 | Common fork answer | +0 pts [-6, +6] | 78 |
| 200 | Full list + descriptions | +0 pts [-6, +6] | 78 |
| 200 | OpenPoke today | +4 pts [-5, +13] | 78 |
| 1,000 | Common fork answer | +3 pts [-5, +10] | 78 |
| 1,000 | Full list + descriptions | +0 pts [-8, +8] | 78 |
| 1,000 | OpenPoke today | +4 pts [-4, +13] | 78 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 30 | 88% | 100% | 100% | 62% | 100% | 100% | 100% | 100% | 100% | 100% |
| OpenPoke today | 200 | 62% | 88% | 100% | 75% | 100% | 100% | 88% | 100% | 88% | 100% |
| OpenPoke today | 1,000 | 38% | 88% | 100% | 75% | 100% | 100% | 100% | 100% | 100% | 100% |
| Full list + descriptions | 30 | 100% | 75% | 100% | 75% | 100% | 100% | 100% | 100% | 88% | 100% |
| Full list + descriptions | 200 | 88% | 88% | 100% | 88% | 100% | 100% | 88% | 100% | 88% | 100% |
| Full list + descriptions | 1,000 | 100% | 88% | 90% | 75% | 100% | 88% | 100% | 100% | 100% | 100% |
| Common fork answer | 30 | 100% | 100% | 100% | 75% | 100% | 75% | 100% | 100% | 88% | 100% |
| Common fork answer | 200 | 100% | 88% | 100% | 75% | 100% | 88% | 88% | 100% | 100% | 100% |
| Common fork answer | 1,000 | 100% | 88% | 100% | 75% | 100% | 62% | 88% | 100% | 100% | 100% |
| Ours (reference-first) | 30 | 100% | 88% | 100% | 88% | 100% | 100% | 88% | 100% | 88% | 100% |
| Ours (reference-first) | 200 | 100% | 88% | 100% | 100% | 100% | 88% | 88% | 100% | 75% | 100% |
| Ours (reference-first) | 1,000 | 88% | 100% | 100% | 100% | 100% | 88% | 88% | 100% | 88% | 75% |

