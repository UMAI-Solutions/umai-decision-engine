# leak — dev

- **Date:** 2026-10-19
- **Git commit:** a639118 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `6f59a1c3d0b3`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.41
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Bundle:** `gr-demo-data-protection` (active version), phase PRE_LLM, destination external_llm, trace on, logs captured at DEBUG
- **Cases:** 407 with at least one entity

## Verdict: PASS

| Where | Cases with a raw value | Must be |
|---|---|---|
| Response and trace (outside the masked text) | 0 | 0 |
| Logs | 0 | 0 |

Decisions: BLOCK 407

## Values that would reach the destination: 0 cases

The decision allowed the content and the value was not masked, so the caller would send
it to the external LLM. Not a leak of the engine's own outputs: each one is an L0 miss
(or an entity type L0 does not support yet) with a real consequence.

None.
