# Evals

Layers are added and tuned on evidence. Every layer has an eval suite, a baseline, and a
report format. An eval is required before: enabling a layer by default, changing a
provider default, changing thresholds, adding a dependency for accuracy reasons.

## Status

Implemented (2026-10-17): the harness (`python -m evals.run`), the `l0-pii-tr` suite with
dataset v2 ([`evals/datasets/l0-pii-tr/`](../../evals/datasets/l0-pii-tr/README.md); every type
of the [L0 entity catalog](../architecture/l0-entity-catalog.md)) and the `leak` suite.
Gateway (2026-10-19): `gateway-latency` and the live `masking-survival` suite. The other suites
below are planned.

```powershell
python -m evals.run --suite l0-pii-tr --split dev --tag <label>   # [--bundle policies/<id>/<v>]
python -m evals.run --suite leak --split dev --tag <label>        # [--bundle-id <id>]
python -m evals.datasets.generate_l0_pii_tr                       # regenerate dataset v2
python -m evals.run --suite gateway-latency --tag <label>         # gateway cost per call, no network
python -m evals.run --suite masking-survival --model gpt-4.1-mini --tag <label>  # live: UMAI_EVAL_OPENAI_API_KEY
```

## Suites

| Suite | Layer | Question it answers | Baseline |
|---|---|---|---|
| `l0-pii-tr` | L0 | Do we find every catalog type (Lakera parity, Türkiye, Europe) precisely? | Per-type targets (ADR-0017); a naive regex baseline is reported for orientation |
| `l0-latency` | L0 | p50/p95/p99 by input size, NER on/off | budget in L0 doc |
| `l1-decisions` | L1 | Correct action for content × context variants? | `umai-engine` context-aware policy (gpt-oss-safeguard) |
| `l1-providers` | L1b | On-prem candidates (d3-flash, clef-flash, kev-4b, generic scoring) vs hosted Jev / OpenAI Decisions: accuracy (TR/EN), calibration, latency on reference hardware | hosted Jev |
| `l1-calibration` | L1b | Do probabilities move when option order changes? | — |
| `l2-chains` | L2 | Do reviews of synthetic sessions find multi-step leaks, without fabricating findings? With vs without history? | L2 without history |
| `pipeline-e2e` | L0 + L1 | Final decision accuracy, runtime latency, review rate | `umai-engine` |
| `l2-worker` | L2 | Queue age and processing time under load; redelivery idempotency | — |
| `gateway-latency` | gateway | What the gateway adds per call (admission, input check over every message, masking, answer check, restoration, streaming modes) and when `window` streams release their first text | budget: well under the upstream's own latency |
| `masking-survival` | gateway | Do models write the gateway's placeholders back intact, so restoration works (kept, altered, dropped, restored per type and task)? Live | — |
| `leak` | all | Does any raw value appear in responses, logs, traces, history, provider payloads? Also: which values an allowed decision would forward unmasked | must be 0 |

## Datasets

Rules:

1. **Synthetic only.** No real customer data, no real people's data, no real identifiers.
   Turkish IDs are generated with valid checksums by the generator in `evals/datasets/`;
   invalid-checksum negatives are generated too.
2. JSONL, one case per line, UTF-8, with a `case_id`, `split` (`dev`/`test`) and `source`
   (`generated`, `handwritten`, `pyrit`, `owasp`).
3. The `test` split is never used for prompt or threshold tuning.
4. Turkish and English cases in every suite; code-switched (TR/EN mixed) cases included.

Case shapes:

```jsonc
// l0-pii-tr
{"case_id": "l0-0001", "split": "dev", "lang": "tr",
 "text": "Müşterinin TC kimlik numarası 10000000146, IBAN TR33 0006 1005 1978 6457 8413 26.",
 "expected": [{"type": "TR_TCKN", "start": 30, "end": 41}, {"type": "TR_IBAN", "start": 48, "end": 80}]}

// l1-decisions: same content, different context → different expected action
{"case_id": "l1-0042", "split": "test", "lang": "tr",
 "request": { /* EvaluationRequest without bundle */ },
 "bundle": "policies/examples/data-protection.yaml",
 "expected_action": "BLOCK", "acceptable": ["BLOCK", "STEP_UP_APPROVAL"]}

// l2-chains: ordered events in one session; expectation on the review of the last one
{"case_id": "l2-0007", "split": "dev", "steps": [ {/* request */}, {/* request */}, {/* request */} ],
 "expected_category": "chain_exfiltration", "expected_related": [0, 1]}
```

Offsets in examples are illustrative; the generator computes real offsets.

Sources to draw from: the OWASP LLM Top-10 attack suite used for the `umai-engine` demo
guardrail, PyRIT-generated attacks (UMAI red-teaming track), handwritten Turkish business
scenarios (banking, telecom, insurance).

## Metrics

| Metric | Definition |
|---|---|
| Entity precision / recall / F1 | per entity type; span match = exact type + overlap ≥ 50 % of the longer span (`evals/metrics.py`) |
| False alarms on clean texts | share of cases without entities where L0 detected something |
| Decision accuracy | final action in `acceptable` set |
| False block rate | expected allow-family, got `BLOCK`/`STEP_UP_APPROVAL` |
| Missed block rate | expected `BLOCK`, got allow-family |
| Review rate | events queued for L2 ÷ all events |
| Chain recall / false-finding rate | L2 findings vs expected chains on synthetic sessions |
| Calibration | expected calibration error; option-order sensitivity (max probability shift across permutations) |
| Latency | p50/p95/p99 per layer and per path, with region and host recorded |
| Cost | L1b cost per 1 000 evaluations; L2 cost at the measured review rate |

## Reports

`python -m evals.run --suite <name> [--provider jev] [--split dev]` writes
`evals/reports/<date>-<suite>-<tag>/` with `summary.md` (committed) and raw per-case
results (not committed; they contain redacted excerpts only, but stay local). Every
summary records: git commit, bundle versions, provider/model ids, region, dataset version.
