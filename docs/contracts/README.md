# Contracts

Versioned data shapes exchanged between the engine, its callers, its layers and its
providers. Until code exists these documents are the source of truth. Once the Pydantic
models in `src/umai_decision_engine/contracts/` exist, code and documents change together
in the same commit (skill `change-contract`).

| Contract | Version | Producer → consumer |
|---|---|---|
| [evaluation-api](evaluation-api.md) | 0.14 | caller ↔ engine |
| [policy-bundle](policy-bundle.md) | 0.9 (draft 0.10) | policy author / loader → engine |
| [entity-detection](entity-detection.md) | 0.2 | L0 → L1, pipeline |
| [decision-questions](decision-questions.md) | 0.4 | bundle → L1b → provider adapters |
| [tagged-action](tagged-action.md) | 0.8 | pipeline → records store (decision events, L1b outcomes; ADR-0041), later session history and the L2 worker |
| [usage](usage.md) | 0.4 | pipeline → usage ledger → usage pages, quotas, invoices (ADR-0041, ADR-0028) |
| [l2-analysis](l2-analysis.md) | 0.1 | L2 model → worker → findings |
| [tag-taxonomy](tag-taxonomy.md) | 0.3 (draft 0.4) | all layers |
| [policy-tests](policy-tests.md) | 0.3 (draft 0.4) | policy author → golden-test runner, CI (ADR-0015) |
| [credentials](credentials.md) | 0.10 | control plane → engines (key registry), control plane ↔ console (key records), console → policy service (on-behalf-of assertion) (ADR-0023) |
| [gateway-api](gateway-api.md) | 0.15 | caller ↔ UMAI gateway: OpenAI Chat Completions, Responses and embeddings surfaces, applications and providers, refusals, streaming modes (ADR-0034, ADR-0039) |
| [entitlements](entitlements.md) | 0.3 | plans file → policy service → engines (tenant snapshot) and console: plans, tenant limits, monthly quota (ADR-0043) |
| [billing](billing.md) | 0.1 | console ↔ policy service: plan, billing details, checkout, plan change, cancel; iyzico → policy service: webhook (ADR-0044) |
| [webhooks](webhooks.md) | 0.2 | console ↔ policy service: endpoints, test, delivery log; policy service → the organization's receivers (generic, signed) and Slack incoming webhooks: blocked decisions per window, quota thresholds, billing changes (ADR-0048) |
| [organizations](organizations.md) | 0.7 | policy service ↔ console: organizations, memberships, invitations, verified domains, the organization archive (ADR-0033, ADR-0047) |
| [data-export](data-export.md) | 0.2 | policy service → console → people: decisions CSV, usage CSV, organization archive (ADR-0047) |
| [console-sessions](console-sessions.md) | 0.1 | console ↔ policy service: a record per sign-in, list, end one, end others, sign-out, the middleware's check (ADR-0024 §2 amendment) |
| [audit](audit.md) | 0.3 | policy service → tenant store → console, auditors: who changed what, when, refused attempts; list and CSV export (ADR-0045) |
| [browser-extension](browser-extension.md) | 0.1 | console ↔ policy service: browser settings and browser connections (a person's own evaluate-only key); console page → extension: the connect handshake; extension → engine: `POST /v1/evaluate` and what the extension does with each answer (ADR-0058) |
| [evaluations](evaluations.md) | draft 0.1 | console ↔ policy service: datasets, built-in sets, runs, cases, report cards, the evaluation gate; evaluation worker → tenant store (ADR-0057, Proposed; not built) |
| [guardrails](guardrails.md) | 0.1 (parts planned) | console ↔ policy service: guardrails, drafts, golden cases, templates, the decision-policy library, publish, activate, archive (ADR-0022, ADR-0056) |

## Versioning rules

- Each contract has its own `MAJOR.MINOR` version, carried in payloads as
  `contract_version` where the shape crosses a process boundary.
- **Minor**: adding an optional field, adding an enum value consumers may ignore.
- **Major**: removing or renaming a field, changing a type or meaning, making a field required.
- During R&D (`0.x`) breaking changes are allowed, but still bump the minor version and add
  a changelog line at the bottom of the document.
- **Drafts.** A change proposed by an ADR that is not implemented yet is written as a draft: a
  `**Draft:** X.Y` line under the version, a `Draft X.Y` section, and a changelog line marked
  `draft`. The `**Version:**` line keeps the implemented version. Draft examples that
  `test_contract_examples.py` would parse against a model use the info strings `yaml draft` /
  `json draft`, because the model does not have the new fields yet; the change that implements the
  draft drops the word `draft`, and the test parses them from then on. In this table a draft shows
  as `(draft X.Y)`; a new contract that is all draft as `draft 0.1`.
- Enum values and field names are `snake_case` except the action / severity / phase enums,
  which keep `umai-engine`'s `UPPER_CASE` values for compatibility.

## Shared enums

| Enum | Values |
|---|---|
| `Phase` | `PRE_LLM`, `POST_LLM`, `TOOL_INPUT`, `TOOL_OUTPUT`, `MCP_REQUEST`, `MCP_RESPONSE`, `MEMORY_WRITE` |
| `Action` | `ALLOW`, `ALLOW_WITH_MODIFICATIONS`, `ALLOW_WITH_WARNINGS`, `STEP_UP_APPROVAL`, `BLOCK` |
| `Severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `Mode` | `ENFORCE`, `MONITOR` |
| `Layer` | `L0`, `L1a`, `L1b`, `L2`, `pipeline` |
| `DestinationClass` | `external_llm`, `internal_llm`, `internal_app`, `external_app`, `agent_tool`, `unknown` |

Phases and actions are identical to `umai-engine` so the cutover adapter is a mapping, not
a translation (see [`../platform-context/compatibility-contract.md`](../platform-context/compatibility-contract.md)).
