# l1-latency — split-beta-westeurope

- **Date:** 2026-10-30
- **Git commit:** f995efb (the container has no git; `src/` and `evals/` were copied from that tree)
- **Dataset:** `evals/datasets/pi-tr/v1.jsonl` (sha256 `6abe7b22463d`), split `all`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.42
- **Host:** Linux 7.0.0-1017-azure, Python 3.12.15, x86_64
- **Provider:** OpenAI Decisions `gpt-6-luna` at `https://api.openai.com/v1`; bundle `gr-demo-data-protection@3` (9 policies, 9 questions; enforcing: `prompt-attack`, `malicious-use`)
- **Where it ran:** umai-beta VM (Azure West Europe, Standard_B2s), one-off container of umai-decision-engine:current (3ffbef3) with this branch's src and evals; smarttech guardrail v3, warm connection
- **Cases:** 200; modes `all`, `split` in alternating order; client timeout 10000 ms (to see the whole distribution); L1b budget 1400 ms

| Mode | Calls per evaluation | p50 ms | p95 ms | p99 ms | max ms | Over budget | Errors | Refused answers | Input tokens / eval | Cached |
|---|---|---|---|---|---|---|---|---|---|---|
| `all` | 1 | 414 | 667 | 795 | 987 | 0/196 | 4 {'timeout': 4} | 0 | 9983 | 0 |
| `split` | 1 (+1 after) | 419 | 617 | 663 | 801 | 0/200 | 0  | 0 | 2509 | 0 |

**`split`, background shadow call** (not on the request path): p50 506 / p95 683 ms, 0 errors, input tokens / eval 8544 (both calls together 11213). The timed call started while the previous case's background call was still running in 85/200 cases.

**Same answer, `split` vs `all`:** 1588/1588 questions (100.0 %), enforcing questions 370/370, over 196 cases.

Prompt-attack probabilities and labels per case are in `results.jsonl` (not committed); thresholds are set in step 3 on the dev split, not here.
