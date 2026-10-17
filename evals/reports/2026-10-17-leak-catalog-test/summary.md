# leak — catalog-test

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `6f59a1c3d0b3`), split `test`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Bundle:** `gr-demo-data-protection` (v3), phase PRE_LLM, destination external_llm, trace on, logs captured at DEBUG
- **Cases:** 141 with at least one entity

## Verdict: PASS

| Where | Cases with a raw value | Must be |
|---|---|---|
| Response and trace (outside the masked text) | 0 | 0 |
| Logs | 0 | 0 |

Decisions: ALLOW 5, ALLOW_WITH_MODIFICATIONS 47, BLOCK 89

## Values that would reach the destination: 5 cases

The decision allowed the content and the value was not masked, so the caller would send
it to the external LLM. Not a leak of the engine's own outputs: each one is an L0 miss
(or an entity type L0 does not support yet) with a real consequence.

- l0-0057: TR_TCKN (decision ALLOW)
- l0-0446: PERSON (decision ALLOW)
- l0-0447: PERSON (decision ALLOW)
- l0-0452: PERSON (decision ALLOW)
- l0-0453: PERSON (decision ALLOW)
