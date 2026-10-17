# Contract: tag taxonomy

**Version:** 0.2

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
| `data.pii.person` | L0 | Person name (gazetteer + surname; NER later, Q8) |
| `data.pii.address` | L0 | Street address (Turkish, US, European formats) |
| `data.pii.phone` | L0 | Non-Turkish phone number (libphonenumber) |
| `data.pii.ip_address` | L0 | IP address (public; private / documentation ranges at lower score) |
| `data.pii.us_ssn` | L0 | US Social Security number |
| `data.pii.tr_plate` | L0 | Turkish vehicle plate (context required) |
| `data.pii.uk_nino`, `data.pii.de_tax_id`, `data.pii.fr_nir`, `data.pii.es_nif`, `data.pii.es_nie`, `data.pii.it_fiscal_code`, `data.pii.nl_bsn`, `data.pii.pl_pesel` | L0 | European national identifiers (checksum-valid) |
| `data.link.unknown` | L0 | URL whose domain is neither popular nor allowed by the guardrail |
| `data.pii.custom.<entity_lower>` | L0 | Tenant custom recognizer |
| `data.pii.bulk` | reserved | Many distinct personal identifiers in one action (L1a never sees detected PII since ADR-0014) |
| `data.secret.api_key` | L0 | API key pattern |
| `data.secret.private_key` | L0 | Private key block |
| `data.secret.jwt` | L0 | JSON Web Token |
| `data.secret.connection_string` | L0 | URI or connection string with embedded credentials |
| `data.secret.password` | L0 | Password value in an assignment or after a password word |
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
- 0.2 (2026-10-17): entity catalog tags (ADR-0017): address, phone, IP, US SSN, plate, EU IDs,
  unknown links, connection string, password.
