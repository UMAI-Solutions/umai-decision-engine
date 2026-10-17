# Contract: policy golden tests

**Version:** 0.2

Golden cases state what a policy bundle version must decide: "this input, in this context →
this decision". They are written by whoever writes the policy, run by
`python -m umai_decision_engine.policy.cli test` and in CI on every change, and later by the
policy control plane's test stage ([ADR-0013](../adr/0013-policy-lifecycle-dashboard.md)).
Decision: [ADR-0015](../adr/0015-policy-golden-tests.md).

## Location

```text
policies/<bundle_id>/<version>/
├── bundle.yaml
├── manifest.json
└── tests/
    ├── l0-gate.yaml          # any number of *.yaml / *.yml files, run in name order
    └── regressions.yaml
```

- `tests/` is **not part of the signed artifact** (the manifest hashes `bundle.yaml` only) and
  the engine never reads it at runtime. Cases can be added to a published version without
  re-signing it, e.g. a regression case for a miss found in the field.
- Case ids are unique within one bundle version, across all its files.
- Synthetic data only (AGENTS.md rule 2). Turkish identifiers come from
  [`turkish-identifiers.md`](../reference/turkish-identifiers.md).

## File shape

```yaml
contract_version: "0.1"
defaults:                                  # optional; merged into every case's input
  phase: PRE_LLM
  destination: { app: chatgpt.com, class: external_llm }
cases:
  - id: tckn-to-external-llm-blocked
    description: A national ID sent to an external LLM is blocked by the L0 gate
    input:
      text: "Müşterinin TC kimlik numarası 10000000146"
    expect:
      action: BLOCK
      decided_by: L0
      entities: [TR_TCKN]

  - id: kyc-internal-allowed
    input:
      text: "TC kimlik 10000000146 için kaydı doğrula"
      subject: { department: KYC }
      destination: { app: kyc-portal, class: internal_app }
    expect:
      action: ALLOW
      reason_contains: ex-kyc-internal

  - id: phone-masked
    input:
      text: "Müşteriyi 0532 123 45 67 numarasından ara"
    expect:
      action: ALLOW_WITH_MODIFICATIONS
      masked: [TR_PHONE]
      redacted_text: "Müşteriyi <TR_PHONE_1> numarasından ara"
```

### `input`

Builds an [evaluation request](evaluation-api.md) for the bundle version the file belongs to.
Fields have the same meaning as in the evaluation API.

| Field | Type | Default | Notes |
|---|---|---|---|
| `text` | string | — | One message: role `user`, or `assistant` when `focus` is `LAST_ASSISTANT_MESSAGE`. Exactly one of `text` / `messages` |
| `messages` | `[{role, content}]` | — | A whole conversation |
| `focus` | `LAST_USER_MESSAGE` \| `LAST_ASSISTANT_MESSAGE` | `LAST_USER_MESSAGE` | |
| `artifacts` | list | `[]` | Tool / MCP / memory artifacts |
| `language` | string | detected | |
| `phase` | `Phase` | `PRE_LLM` | |
| `subject` | object | empty | `user_id`, `role`, `department`, `agent_id`, `session_id` |
| `destination` | object | `class: unknown` | `app`, `class` |
| `action` | object | none | Agent action: `name`, `tool_name`, `server_name`, `method`, `memory_scope`, `metadata` |

`defaults` takes the same fields except `text` and `messages`. A field set in the case
replaces the default field as a whole (no deep merge: a case `subject` replaces the default
`subject`).

The runner fills the rest: tenant `golden-tests`, the bundle id and version of the directory,
and a generous timeout. Golden cases check decisions, not latency.

### `expect`

Every field is optional except the decision: exactly one of `action` / `acceptable`.
List fields are compared as sets (order does not matter); an empty list asserts "none".

| Field | Checks |
|---|---|
| `action` | `decision.action` equals this |
| `acceptable` | `decision.action` is one of these (for model-backed decisions later) |
| `decided_by` | `decision.decided_by`: `L0`, `L1a`, `L1b`, `pipeline` |
| `severity` | `decision.severity` |
| `entities` | Exactly these entity types were detected (`response.entities[].type`) |
| `masked` | Exactly these types were masked (`output_modifications.entities`; `[]` = no modifications) |
| `redacted_text` | `output_modifications.redacted_text` equals this |
| `reason_contains` | `decision.reason` contains this text (e.g. an exception id) |
| `would_have` | `MONITOR` mode: `would_have.action` |
| `errors` | Exactly these error types (`errors[].type`), e.g. `[L1_NOT_IMPLEMENTED]` |
| `degraded` | `response.degraded` |
| `matched` | Exactly these policy / exception / rule ids in `decision.matched` (0.2) |

Load-time checks (a failing check fails the run, like a failing case):

- the file parses with this contract (unknown keys are errors, so a typo in `expect` is
  never silently ignored);
- case ids are unique within the bundle version;
- every type in `entities` / `masked` is enabled in the bundle's `l0.entities` or
  `l0.custom_recognizers`.

## Runner output

Case ids, expected and actual decision fields only. The input text is never printed; a
`redacted_text` mismatch prints the redacted output.

```text
gr-demo-data-protection v2 (active): 9 cases
  ok    tckn-to-external-llm-blocked
  FAIL  kyc-internal-allowed  [tests/l0-gate.yaml]
        action: expected ALLOW, got BLOCK
gr-demo-data-protection v1: no golden tests
2 versions, 9 cases: 8 passed, 1 failed
```

Exit code 0 when every loaded version is valid and every case passes; 1 otherwise. With
`--require-tests`, an active version without golden tests also fails.

## Changelog

- 0.2 (2026-10-17): `expect.matched` (ADR-0021); files may declare `0.1` or `0.2`.
- 0.1 (2026-10-17): initial version (ADR-0015).
