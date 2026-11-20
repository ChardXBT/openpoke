# Agent-overload eval results

## Model: `anthropic/claude-haiku-5.5`

| Agents | Setup | n | Correct [95% CI] | Duplicate | Wrong reuse | False reuse | Asked when ambiguous | Over-asked | Prompt tokens/turn | p50 s | $/turn |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | OpenPoke today | 105 | 86% [79–92] | 6% | 6% | 15% | – | 4% | 22,830 | 3.5 | 0.0026 |
| 200 | Common fork answer | 19 | 95% [84–100] | 0% | 0% | – | – | 5% | 17,947 | 9.0 | 0.0020 |

### Correct rate by scenario family

| Setup | Agents | 0 | Needle | "send it" / "did she reply" | Looks ambiguous, is clear | New task, look-alikes | Inbound email |
|---|---:|---:|---:|---:|---:|---:|---:|
| OpenPoke today | 200 | 40% | 90% | 94% | 94% | 85% | 100% |
| Common fork answer | 200 | – | 94% | – | – | – | 100% |

