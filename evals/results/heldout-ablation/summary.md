# Agent-overload eval results

## Model: `anthropic/claude-haiku-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | Ours (reference-first) | 62 | 95% [89–100] | 2% | 0% | 0% | 100% | 4% | 17,902 | 2.8 | 0.0020 |
| 1,000 | Ours without ask-guard | 62 | 94% [87–98] | 2% | 2% | 0% | 100% | 4% | 17,466 | 2.8 | 0.0019 |
| 1,000 | Ours without related | 62 | 90% [82–97] | 8% | 0% | 0% | 100% | 4% | 20,330 | 3.2 | 0.0022 |
| 1,000 | Ours without resolver | 62 | 98% [95–100] | 2% | 0% | 0% | 100% | 0% | 18,375 | 3.2 | 0.0021 |

### Correct rate by scenario family

| Setup | Agents | Needle | Same person, other topic | "send it" / "did she reply" | Truly ambiguous | Looks ambiguous, is clear | New task, look-alikes | Cross-client | Inbound email | Archived | Repeat after clarifying |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Ours (reference-first) | 1,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% |
| Ours without ask-guard | 1,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 50% |
| Ours without related | 1,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 50% | 67% |
| Ours without resolver | 1,000 | 83% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |

