# leak — baseline-dev

- **Date:** 2026-10-17
- **Git commit:** 0f0b420 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v1.jsonl` (sha256 `1e4c317836e5`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Bundle:** `gr-demo-data-protection` (active version), phase PRE_LLM, destination external_llm, trace on, logs captured at DEBUG
- **Cases:** 139 with at least one entity

## Verdict: PASS

| Where | Cases with a raw value | Must be |
|---|---|---|
| Response and trace (outside the masked text) | 0 | 0 |
| Logs | 0 | 0 |

Decisions: ALLOW 21, ALLOW_WITH_MODIFICATIONS 42, BLOCK 76

## Values that would reach the destination: 21 cases

The decision allowed the content and the value was not masked, so the caller would send
it to the external LLM. Not a leak of the engine's own outputs: each one is an L0 miss
(or an entity type L0 does not support yet) with a real consequence.

- l0-0036: TR_TCKN (decision ALLOW)
- l0-0038: TR_TCKN (decision ALLOW)
- l0-0040: TR_TCKN (decision ALLOW)
- l0-0051: TR_TCKN (decision ALLOW)
- l0-0055: TR_TCKN (decision ALLOW)
- l0-0056: TR_TCKN (decision ALLOW)
- l0-0096: TR_PHONE (decision ALLOW)
- l0-0114: TR_IBAN (decision ALLOW)
- l0-0156: EMAIL_ADDRESS (decision ALLOW)
- l0-0239: TR_VKN (decision ALLOW)
- l0-0241: TR_VKN (decision ALLOW)
- l0-0242: TR_VKN (decision ALLOW)
- l0-0243: TR_VKN (decision ALLOW)
- l0-0244: PERSON (decision ALLOW)
- l0-0245: PERSON (decision ALLOW)
- l0-0246: PERSON (decision ALLOW)
- l0-0247: PERSON (decision ALLOW)
- l0-0248: PERSON (decision ALLOW)
- l0-0249: PERSON (decision ALLOW)
- l0-0250: PERSON (decision ALLOW)
- l0-0251: PERSON (decision ALLOW)
