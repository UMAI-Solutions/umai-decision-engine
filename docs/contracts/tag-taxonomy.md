# Contract: tag taxonomy

**Version:** 0.1

Tags are dotted, lowercase codes. Each code lists the layers allowed to emit it. Adding a
code is a minor version bump; renaming or removing one is major.

## `data.*` — what data is involved

| Code | Emitted by | Meaning |
|---|---|---|
| `data.pii.tr_tckn` | L0 | Turkish national ID detected and checksum-valid |
| `data.pii.tr_vkn` | L0 | Turkish tax ID detected and checksum-valid |
| `data.pii.tr_iban` | L0 | Turkish IBAN detected and mod-97-valid |
| `data.pii.iban` | L0 | Non-Turkish IBAN |
| `data.pii.tr_phone` | L0 | Turkish phone number |
| `data.pii.credit_card` | L0 | Luhn-valid card number |
| `data.pii.email` | L0 | Email address |
| `data.pii.person` | L0 | Person name (NER; only when enabled) |
| `data.pii.custom.<entity_lower>` | L0 | Tenant custom recognizer |
| `data.pii.bulk` | reserved | Many distinct personal identifiers in one action (L1a never sees detected PII since ADR-0014) |
| `data.secret.api_key` | L0 | API key pattern |
| `data.secret.private_key` | L0 | Private key block |
| `data.secret.jwt` | L0 | JSON Web Token |
| `data.external_disclosure` | L1b | Data leaves the organisation |

L0 emits one `data.*` tag per entity type present (not per occurrence), with the maximum score.

## `intent.*` — what the actor is trying to do

| Code | Emitted by |
|---|---|
| `intent.data_exfiltration` | L1b, L2 |
| `intent.prompt_injection` | L1b, L2 |
| `intent.jailbreak` | L1b, L2 |
| `intent.policy_evasion` | L1b, L2 |

## `action.*` — what the agent/tool action does

| Code | Emitted by |
|---|---|
| `action.destructive` | L1a |
| `action.privilege_change` | L1a |
| `action.bulk_export` | L1a |
| `action.external_send` | L1a |

## `destination.*`

| Code | Emitted by |
|---|---|
| `destination.external_llm` | L1a |
| `destination.unsanctioned_app` | L1a |

## `chain.*` — multi-step patterns (session history)

Emitted only in L2 analyses (asynchronous); they never appear on runtime decisions.

| Code | Emitted by |
|---|---|
| `chain.exfiltration` | L2 |
| `chain.escalating_risk` | L2 |

## Changelog

- 0.1 (2026-10-15): initial draft.
