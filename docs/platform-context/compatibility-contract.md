# Compatibility notes for a future replacement

Background only. If this engine ever replaces `umai-engine` (roadmap phase F4), callers of
`umai-engine` should not have to change their models. This file lists what that would
require. Nothing here is built or connected now; just avoid design choices that would make
it impossible.

## Endpoint

`POST /internal/ai-engine/v1/evaluate` and `GET /healthz`, served by
`contracts/compat/umai_engine.py` adapters in front of the native `/v1/evaluate`.

## `InternalRequest` (accepted as is)

| Field | Type | Maps to |
|---|---|---|
| `request_id` | str | `request_id` |
| `timestamp` | str | `timestamp` |
| `tenant_id`, `environment_id`, `project_id` | str | `tenant_id`; env/project are part of the bundle lookup key |
| `guardrail_id`, `guardrail_version` | str, int | `bundle.id`, `bundle.version` |
| `phase` | `PRE_LLM`, `POST_LLM`, `TOOL_INPUT`, `TOOL_OUTPUT`, `MCP_REQUEST`, `MCP_RESPONSE`, `MEMORY_WRITE` | `phase` (identical) |
| `input.messages[]` | `{role: system\|user\|assistant, content}` | `content.messages[]` |
| `input.phase_focus` | `LAST_USER_MESSAGE` \| `LAST_ASSISTANT_MESSAGE` | `content.focus` |
| `input.content_type`, `input.language` | | same |
| `input.artifacts[]` | `{artifact_type, name, payload_summary, content, content_type, metadata}` | `content.artifacts[]` |
| `timeout_ms` | int, default 1500 | `timeout_ms` |
| `flags.allow_llm_calls` | bool | `flags.allow_model_calls` |
| `agent_context` | dict \| null | `subject.agent_id`, `action.*`. AGT metadata (`agent_id`, `action`, `tool_name`, `server_name`, `method`, `memory_scope`, plus `agent_did`, `trust_score`, `trust_tier`, `capabilities`) is read from `input.artifacts[0].metadata` first, then from `agent_context`; `run_id` and `step_id` come from `agent_context`. The adapter must use the same precedence |

`subject` (role, department) and `destination` do not exist in `InternalRequest`. Until
`umai-service` sends them, they evaluate as absent; the compat adapter must not invent them.

## `InternalResponse` (returned as is)

| Field | Source |
|---|---|
| `request_id`, `tenant_id`, `environment_id`, `project_id`, `guardrail_id`, `guardrail_version`, `phase` | echoed |
| `decision {action, allowed, severity, reason}` | `decision` (drop `decided_by`) |
| `triggering_policy {policy_id, type, name, status, severity, score, details, latency_ms}` | from the deciding layer: `type` = `HEURISTIC` for L0/L1a, `CONTEXT_AWARE` for L1b, `AGT` for agent rules; `details` carries tags, `decided_by`, `event_id` |
| `output_modifications` | `{policy_id, modified_text, replacement, details}` from L0 redaction |
| `latency_ms {total, preflight}` | `total`; `preflight` = L0 latency |
| `errors[] {type, source, message, retryable}` | `errors[]` (`source` = layer) |

Action values must stay within: `ALLOW`, `ALLOW_WITH_MODIFICATIONS`,
`ALLOW_WITH_WARNINGS`, `STEP_UP_APPROVAL`, `BLOCK`. Severity within `LOW`, `MEDIUM`,
`HIGH`, `CRITICAL`.

## `GuardrailSnapshot` → policy bundle conversion

A converter (F4) produces a bundle from an existing snapshot:

| Snapshot | Bundle |
|---|---|
| `guardrail_id`, `version`, `mode`, `phases` | same |
| `preflight` (HeuristicConfig) | `l0.legacy_preflight` |
| `policies[type=HEURISTIC]` | `l0.legacy_preflight`-style rules (kept as-is) |
| `policies[type=CONTEXT_AWARE]` | `l1b.policies[]` with a generated question set (`violation` condition, `policy_category` choice); manual review required |
| `llm_config` | settings model registry entry; bundle gets `model_ref` |
| `agt.policy_document.rules` | `l1a.rules` (conditions translated to fact paths) |
| `signature`, `key_id` | re-signed by `umai-service` |

## Behaviours that must not regress

- Missing snapshot/bundle → `BLOCK` with `CONFIG_MISSING`.
- `MONITOR` never blocks.
- `flags.allow_llm_calls=false` performs no model call.
- Fail-closed default when evaluation cannot complete.
- Snapshot signature verification when `SNAPSHOT_SIGNING_KEY` is set.
