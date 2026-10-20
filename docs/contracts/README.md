# Contracts

Versioned data shapes exchanged between the engine, its callers, its layers and its
providers. Until code exists these documents are the source of truth. Once the Pydantic
models in `src/umai_decision_engine/contracts/` exist, code and documents change together
in the same commit (skill `change-contract`).

| Contract | Version | Producer → consumer |
|---|---|---|
| [evaluation-api](evaluation-api.md) | 0.9 | caller ↔ engine |
| [policy-bundle](policy-bundle.md) | 0.8 | policy author / loader → engine |
| [entity-detection](entity-detection.md) | 0.2 | L0 → L1, pipeline |
| [decision-questions](decision-questions.md) | 0.3 | bundle → L1b → provider adapters |
| [tagged-action](tagged-action.md) | 0.2 | L1 → event sink, session history, L2 worker |
| [l2-analysis](l2-analysis.md) | 0.1 | L2 model → worker → findings |
| [tag-taxonomy](tag-taxonomy.md) | 0.1 | all layers |
| [policy-tests](policy-tests.md) | 0.2 | policy author → golden-test runner, CI (ADR-0015) |
| [credentials](credentials.md) | 0.3 | control plane → engines (key registry), control plane ↔ console (key records), console → policy service (on-behalf-of assertion) (ADR-0023) |
| [organizations](organizations.md) | 0.2 | policy service ↔ console: organizations, memberships, invitations, verified domains (ADR-0033) |

## Versioning rules

- Each contract has its own `MAJOR.MINOR` version, carried in payloads as
  `contract_version` where the shape crosses a process boundary.
- **Minor**: adding an optional field, adding an enum value consumers may ignore.
- **Major**: removing or renaming a field, changing a type or meaning, making a field required.
- During R&D (`0.x`) breaking changes are allowed, but still bump the minor version and add
  a changelog line at the bottom of the document.
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
