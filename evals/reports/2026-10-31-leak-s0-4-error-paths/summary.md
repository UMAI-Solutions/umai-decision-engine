# leak — s0-4-error-paths

- **Date:** 2026-10-31
- **Git commit:** 1cac298 (uncommitted changes)
- **Dataset:** `evals/datasets/l0-pii-tr/v2.jsonl` (sha256 `6f59a1c3d0b3`), split `dev`
- **Engine:** umai-decision-engine 0.1.0, presidio-analyzer 2.2.364, phonenumbers 9.0.42
- **Host:** Windows 11, Python 3.12.6, AMD64
- **Bundle:** `gr-demo-data-protection` (active version), phase PRE_LLM, destination external_llm, trace on, logs captured at DEBUG
- **Cases:** 407 with at least one entity
- **Error paths:** 150 probes

## Verdict: PASS

| Where | Cases with a raw value | Must be |
|---|---|---|
| Response and trace (outside the masked text) | 0 | 0 |
| Logs | 0 | 0 |
| Stored records (decision events, L1b outcomes, usage) | 0 | 0 |
| Error answers and their logs (probes) | 0 | 0 |

Decisions: BLOCK 407

## Values that would reach the destination: 0 cases

The decision allowed the content and the value was not masked, so the caller would send
it to the external LLM. Not a leak of the engine's own outputs: each one is an L0 miss
(or an entity type L0 does not support yet) with a real consequence.

None.

## Error paths

Every probe carries synthetic raw values of every dataset type and a canary, and is refused or fails on purpose (see `evals/suites/leak_errors.py`).

| Surface | Probes | 4xx | 5xx | Other | Answers with a raw value | Logs with a raw value | Caller content echoed by UMAI | Must be |
|---|---|---|---|---|---|---|---|---|
| Engine API | 24 | 18 | 2 | 4 | 0 | 0 | 0 | 0 |
| Gateway | 33 | 24 | 7 | 2 | 0 | 0 | 0 | 0 |
| Policy service | 93 | 92 | 1 | 0 | 0 | 0 | 0 | 0 |

Provider errors the gateway passes through by contract (an error body with `X-Umai-Upstream-Error`, or an error event mid-stream): 3 probes. The scripted provider echoes what it received: masked content; raw values in the echo, which L0 left unmasked and the provider therefore received (L0 misses, not error-path leaks): PHONE_NUMBER.

Error codes covered: engine 200 decision:ALLOW, engine 200 decision:BLOCK, engine 401 invalid_credential, engine 403 guardrail_mismatch, engine 403 insufficient_scope, engine 403 ip_not_allowed, engine 403 tenant_mismatch, engine 413 input_too_large, engine 422 bundle_required, engine 422 invalid_request, engine 429 quota_exceeded, engine 429 rate_limited, engine 429 too_many_failed_attempts, engine 500 -, engine 503 detail_text, gateway 200 -, gateway 400 application_required, gateway 400 invalid_request, gateway 400 provider_key_required, gateway 400 umai_blocked, gateway 401 invalid_credential, gateway 403 application_mismatch, gateway 403 insufficient_scope, gateway 403 ip_not_allowed, gateway 413 input_too_large, gateway 413 request_too_large, gateway 429 quota_exceeded, gateway 429 rate_limited, gateway 429 too_many_failed_attempts, gateway 500 -, gateway 500 invalid_request, gateway 502 upstream_invalid_response, gateway 502 upstream_unreachable, gateway 504 upstream_timeout.

Policy service answers: HTTP 401 × 21, HTTP 403 × 6, HTTP 422 × 65, HTTP 500 × 1 over 30 routes.

