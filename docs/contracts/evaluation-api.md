# Contract: evaluation API

**Version:** 0.9 · **Endpoints:** `POST /v1/evaluate`, `GET /v1/bundles`, `POST /v1/bundles/reload`, `POST /v1/detect` (development) · **Health:** `GET /healthz`, `GET /readyz`

`/readyz` returns 200 only after Presidio analyzers are loaded and warmed up, the policy
bundles are loaded, and the configured L1b provider answered a probe.

The response is the **final runtime decision** (L0 + L1). L2 review happens later and is
not part of this API ([ADR-0010](../adr/0010-l2-asynchronous-review.md)).

## Request — `EvaluationRequest`

```json
{
  "contract_version": "0.6",
  "request_id": "req-01J9X7Z8QK",
  "timestamp": "2026-10-15T09:30:00Z",
  "tenant_id": "tenant-demo",
  "bundle": { "id": "gr-demo-data-protection", "version": 3 },
  "phase": "PRE_LLM",
  "content": {
    "messages": [
      { "role": "user", "content": "Bu listeyi özetle: <synthetic text with TCKN 10000000146>" }
    ],
    "focus": "LAST_USER_MESSAGE",
    "artifacts": [],
    "content_type": "text",
    "language": "tr"
  },
  "subject": {
    "user_id": "u-123", "role": "sales", "department": "SALES",
    "agent_id": null, "session_id": "s-456"
  },
  "destination": { "app": "chatgpt.com", "class": "external_llm" },
  "action": null,
  "timeout_ms": 1500,
  "flags": { "allow_model_calls": true, "trace": true }
}
```

| Field | Type | Req. | Notes |
|---|---|---|---|
| `contract_version` | string | yes | `"0.6"` (`"0.2"`–`"0.5"` still accepted) |
| `request_id` | string | yes | Echoed; used as trace id |
| `timestamp` | RFC 3339 string | yes | Caller time |
| `tenant_id` | string | no (0.6) | The credential's tenant is authoritative (ADR-0023 §5): omitted → filled from the API key; present → must equal it, else 403 `tenant_mismatch`. Required with the shared operator token (400 `tenant_required`). Selects HMAC key and history namespace |
| `bundle.id` | string | yes | Policy bundle to evaluate against |
| `bundle.version` | int | no | Explicit version; omitted → the bundle's **active** version (ADR-0012) |
| `bundle_inline` | PolicyBundle | no | R&D only: evaluate against an inline bundle; disabled unless `settings.allow_inline_bundles` |
| `phase` | Phase | yes | |
| `content.messages[]` | `{role: system\|user\|assistant, content}` | yes | Same as `umai-engine` `ChatMessage` |
| `content.focus` | `LAST_USER_MESSAGE` \| `LAST_ASSISTANT_MESSAGE` | yes | Primary evaluation target |
| `content.artifacts[]` | Artifact | no | Same shape as `umai-engine` `InputArtifact` (`artifact_type`, `name`, `payload_summary`, `content`, `content_type`, `metadata`) |
| `content.content_type` | `text` \| `markdown` \| `json` | no | default `text` |
| `content.language` | ISO 639-1 | no | Hint; L0 detects if absent |
| `subject` | Subject | no | All fields optional; missing facts evaluate as absent in L1a |
| `destination` | `{app, class: DestinationClass}` | no | `class` defaults to `unknown` |
| `action` | `{name, tool_name, server_name, method, memory_scope, metadata}` | for agent phases | Required keys per phase as in `umai-engine` AGT: `TOOL_INPUT` → `name`, `tool_name`; `MCP_REQUEST` → `name`, `server_name`, `method`; `MEMORY_WRITE` → `name`, `memory_scope` |
| `timeout_ms` | int | no | Overall runtime budget; capped by settings |
| `flags.allow_model_calls` | bool | no | `false` skips L1b (L0 + L1a only); `degraded: true` if an L1b policy applied |
| `flags.trace` | bool | no | Include the per-layer `trace` in the response |

Which content L0 scans is set by `l0.target` in the bundle (`LAST_MESSAGE`,
`FULL_HISTORY`, `ATTACHMENTS`, `FULL_CONTEXT`, same values as `umai-engine`).

## Response — `EvaluationResponse`

```json
{
  "contract_version": "0.6",
  "request_id": "req-01J9X7Z8QK",
  "tenant_id": "tenant-demo",
  "bundle": { "id": "gr-demo-data-protection", "version": 3, "mode": "ENFORCE", "sha256": "<hex sha256 of bundle.yaml>" },
  "phase": "PRE_LLM",
  "decision": {
    "action": "BLOCK",
    "allowed": false,
    "severity": "HIGH",
    "reason": "L0: TR_TCKN detected",
    "decided_by": "L0",
    "matched": [
      { "kind": "l0_on_detect", "id": "on_detect.default", "action": "BLOCK", "entities": ["TR_TCKN"] }
    ]
  },
  "would_have": null,
  "degraded": false,
  "tags": [
    { "code": "data.pii.tr_tckn", "layer": "L0", "score": 0.97 }
  ],
  "entities": [ { "type": "TR_TCKN", "count": 1, "distinct": 1 } ],
  "output_modifications": null,
  "event_id": "ta-01J9X7Z9AB",
  "review_queued": false,
  "latency_ms": { "total": 10.1, "l0": 9.8 },
  "trace": [
    { "layer": "L0", "outcome": "BLOCK", "latency_ms": 9.8 }
  ],
  "errors": []
}
```

| Field | Notes |
|---|---|
| `decision.action`, `allowed`, `severity`, `reason` | Same meaning as `umai-engine` `Decision` |
| `bundle` | Id, version, mode and content hash of the bundle that decided (every decision is traceable to an exact bundle) |
| `decision.decided_by` | `L0` (an entity was detected: the L0 gate decided and L1 did not run, ADR-0014), `L1a`, `L1b`, or `pipeline` (failure policy) |
| `decision.reason` | Entity types and exception ids only, never values: `L0: TR_TCKN, CREDIT_CARD detected`, `L0: TR_TCKN allowed by exception ex-kyc-internal` |
| `decision.matched[]` | What produced the decision, strictest first: `{kind, id, version?, name?, action, entities[]}` with `kind` `l0_policy` (guardrail policy, ADR-0021), `l0_on_detect` (bundles ≤ 0.5), `l0_exception`, `l1a_rule`. Ids only, never content |
| `would_have` | In `MONITOR` mode: the decision `ENFORCE` would have returned; `decision` is then `ALLOW` |
| `degraded` | Any failure path or fallback was taken; details in `errors` |
| `tags[]` | See [tag-taxonomy](tag-taxonomy.md) |
| `entities[]` | Types and counts only; no values, no spans, no hashes |
| `output_modifications` | `{ "redacted_text": "...", "entities": [...types] }` when action is `ALLOW_WITH_MODIFICATIONS`: the scanned text with exactly those entity types replaced by placeholders (types allowed by an exception stay as written) |
| `event_id` | Id of the decision event; later L2 analyses and findings reference it |
| `review_queued` | The event was selected for asynchronous L2 review (informational; the decision is final either way) |
| `trace[]` | Only when `flags.trace`; never contains content |
| `errors[]` | `{layer, type, message, retryable}`; messages never contain content |

The response never contains raw content. `reason` is leak-checked.

## Authentication and admission

Every `/v1` route requires `Authorization: Bearer <credential>`; `/healthz` and `/readyz` never
do. Two credential types ([ADR-0023](../adr/0023-identity-planes-and-api-keys.md), contract
[credentials](credentials.md)):

| Credential | Tenant | Scopes | Where |
|---|---|---|---|
| **Per-tenant API key** `umai_<env>_<region>_<key_id>_<secret>` from the control plane, resolved against the signed key registry (`UMAI_DE_KEY_REGISTRY_DIR`) | the key's tenant | the key's scopes (default `evaluate`) | SaaS and self-hosted |
| **Shared operator token** `UMAI_DE_API_TOKEN` ([ADR-0016](../adr/0016-control-center-separate-repo.md)) | the body's `tenant_id` | all | self-hosted single-customer installs; unset in SaaS |

Outside `development` the API refuses to start unless at least one is configured.
Credentials are held by server-side callers only (SDKs, the console's backend), never by browsers.

| Route | Scope |
|---|---|
| `POST /v1/evaluate`, `POST /v1/detect` | `evaluate` |
| `GET /v1/bundles`, `GET /v1/bundles/{id}/versions/{version}` | `bundles:read` |
| `POST /v1/bundles/reload` | `bundles:reload` (operator token only) |

Admission answers 401 `invalid_credential`, 403 `tenant_mismatch` / `insufficient_scope` and
429 `rate_limited` (`Retry-After`; token bucket per credential, `UMAI_DE_RATE_LIMIT_RPS` /
`_BURST`) with a JSON body `{"detail": {"error": "<code>", "detail": "<text>"}}`; a
refused request is never evaluated.

**Bundles belong to a tenant** (ADR-0019 §2, [policy-bundle](policy-bundle.md) 0.8). An
evaluation resolves `bundle.id` among its own tenant's bundles only; another tenant's bundle id
resolves nothing (`CONFIG_MISSING`, the configured failure policy decides). The bundle routes
read one tenant: the key's, or for the shared operator token the `X-Umai-Tenant` header (default:
the deployment's default tenant, `UMAI_DE_DEFAULT_TENANT_ID`). A key that sends `X-Umai-Tenant`
for another tenant gets 403 `tenant_mismatch`.

## `GET /v1/bundles`

One tenant's loaded versions per bundle id, the active version and signature status. Used by
operators and the policy dashboard to confirm a published version is live (ADR-0013). Load
errors (locations, never content) are listed for the shared operator token only; keys get an
empty list.

```json
{
  "source": "directory",
  "tenant_id": "tenant-demo",
  "reloaded_at": "2026-10-16T09:00:00+00:00",
  "bundles": [ { "id": "gr-demo-data-protection", "active_version": 1,
                 "versions": [ { "version": 1, "sha256": "…", "signed": false, "key_id": null,
                               "mode": "ENFORCE", "loaded_at": "…" } ] } ],
  "errors": [ { "location": "…", "error": "…", "at": "…" } ]
}
```

`POST /v1/bundles/reload` rescans the source immediately (the "new version available" signal)
and returns `{"changed": true|false}`. The poller does the same every
`UMAI_DE_BUNDLE_POLL_INTERVAL_S` seconds.

## `GET /v1/bundles/{id}/versions/{version}`

One loaded guardrail version of the caller's tenant with its content (policy data; bundles
never contain secrets, ADR-0012) for the console's guardrail view: `{id, version, tenant_id,
active, sha256, signed, key_id, loaded_at, bundle}`. 404 when that version is not loaded for
that tenant, including when it belongs to another tenant.

## `POST /v1/detect` (development only)

Runs L0 only for an `EvaluationRequest` and returns entity types, offsets, scores,
placeholders, the summary and the redacted text — never values or hashes. Enabled by
`UMAI_DE_EXPOSE_DETECT_ENDPOINT` (default: only in development); 404 otherwise.

## Inline bundles

`bundle_inline` evaluates a request against a bundle in the request body (playground). It is
rejected with HTTP 400 unless `UMAI_DE_ALLOW_INLINE_BUNDLES` (default: only in development).
An inline bundle that fails validation is answered with `BLOCK` / `CONFIG_INVALID` and the
validation problems in `errors[].message`.

## Errors

The endpoint returns HTTP 200 with a decision for every evaluable request, including
failure-policy decisions. HTTP 4xx only for malformed requests (schema violations) and admission
(401, 403, 429; see above), HTTP 503 only before readiness.

A malformed request (invalid JSON, a missing or unknown field, a wrong type) gets **422** in the
admission error shape plus `problems`: where the request is malformed (`loc`) and how (`type`,
Pydantic's error type), at most 50 entries. The body never contains the caller's input: no
`input`, `ctx`, `msg` or `url`, and a location keeps only the contract's field names and list
positions; any other key (an unknown field, a `metadata` key) is shown as `<key>` (ADR-0005).
Authentication runs first, so only an admitted caller sees a 422.

**Prompt size** ([ADR-0019](../adr/0019-editions-as-runtime-entitlements.md) §4 and its
amendment): the characters (Unicode code points) of every `content.messages[].content` and
every `content.artifacts[].content`, added up. Above the limit (`UMAI_DE_MAX_INPUT_CHARS`,
default 32 000, until per-tenant entitlements carry `max_input_chars`) `POST /v1/evaluate` and
`POST /v1/detect` answer **413** in the admission error shape with the limit and the measured
size. Nothing is evaluated and nothing is truncated: the caller's own failure behaviour
applies, visibly. The check runs after admission and schema validation.

```json
{
  "detail": {
    "error": "input_too_large",
    "detail": "the content has 41250 characters; this deployment accepts at most 32000",
    "limit": 32000,
    "size": 41250,
    "unit": "characters"
  }
}
```

```json
{
  "detail": {
    "error": "invalid_request",
    "detail": "the request does not match the evaluation API contract",
    "problems": [
      { "loc": ["body", "timestamp"], "type": "missing" },
      { "loc": ["body", "content", "<key>"], "type": "extra_forbidden" }
    ]
  }
}
```

## Changelog

- 0.9 (2026-10-19): prompt-size limit: HTTP 413 `input_too_large` with `limit`, `size` and
  `unit: "characters"` above `UMAI_DE_MAX_INPUT_CHARS` (default 32 000) over all message and
  artifact content, on `/v1/evaluate` and `/v1/detect` (gap T2; ADR-0019 amendment, Q19).
  Request shape unchanged; requests 0.2–0.9 accepted.
- 0.8 (2026-10-19): HTTP 422 body defined: `{"detail": {"error": "invalid_request", "detail",
  "problems": [{"loc", "type"}]}}`, without the caller's input (gap L1); previously FastAPI's
  default body, which echoed the offending value. Request shape unchanged; requests 0.2–0.8
  accepted.
- 0.7 (2026-10-19): bundles are tenant-owned: evaluation resolves only the request tenant's
  bundles; `/v1/bundles` and `/v1/bundles/{id}/versions/{version}` read one tenant (the key's,
  or `X-Umai-Tenant` / the default tenant for the operator token) and carry `tenant_id`; load
  errors only for the operator token. Request shape unchanged; requests 0.2–0.7 accepted.
- 0.6 (2026-10-18): per-tenant API keys and scopes at admission (ADR-0023): `tenant_id` optional
  and derived from the credential; 403 `tenant_mismatch` / `insufficient_scope`, 429
  `rate_limited`, 400 `tenant_required`; error bodies carry an `error` code. Requests 0.2–0.6 accepted.
- 0.5 (2026-10-17): `decision.matched` (provenance, ADR-0021); guardrail `name` in
  `/v1/bundles`; `GET /v1/bundles/{id}/versions/{version}`. Requests 0.2–0.5 accepted.
- 0.4 (2026-10-17): bearer-token authentication on `/v1` routes (ADR-0016); no payload change.
- 0.4 (2026-10-16): `decided_by: "L0"`; L0 gate reasons; `output_modifications` from the L0
  gate (ADR-0014).
- 0.3 (2026-10-16): `bundle.version` optional (active pointer); response `bundle.sha256`;
  `/v1/bundles`, `/v1/bundles/reload`, development `/v1/detect`; inline-bundle rules.
- 0.2 (2026-10-15): breaking — removed synchronous-L2 fields (`escalated`, `l2_override`,
  `latency_ms.history`, `latency_ms.l2`, `decided_by: L2`); renamed `tagged_action_id` to
  `event_id`; added `review_queued` (ADR-0010).
- 0.1 (2026-10-15): initial draft.
