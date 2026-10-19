# Contract: gateway API

**Version:** 0.1 · **Status:** defined, not implemented. WP2 builds non-streaming calls, routes,
input and answer checks, masking and refusals; WP3 streaming and restoration; WP5 tool checks
([design](../architecture/ai-gateway.md) §8) · **Endpoints** (gateway host, e.g.
`eu.gateway.<domain>`): `POST /v1/chat/completions`, `GET /v1/models` · **Health:** `GET /healthz`,
`GET /readyz` · **Decisions:** [ADR-0034](../adr/0034-umai-secure-gateway.md),
[ADR-0035](../adr/0035-raw-content-zone-l0-and-gateway.md),
[ADR-0036](../adr/0036-checks-continue-after-masking.md)

The gateway speaks the provider's wire format, OpenAI Chat Completions in 0.1. It reads and may
rewrite only the fields this document names; every other request and response field passes through
unchanged. Provider shapes were checked on 2026-10-19 against OpenAI's
[Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create),
[streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
and [error codes](https://developers.openai.com/api/docs/guides/error-codes); re-verify before
changing an implementation (AGENTS rule 7). Until the gateway exists this document is the source of
truth; Pydantic models for routes and gateway errors arrive with WP2.

## Headers

| Request header | Required | Meaning |
|---|---|---|
| `Authorization: Bearer <UMAI key>` | yes | Per-tenant API key with scope `gateway` ([credentials](credentials.md); the scope is added with WP2). The tenant comes from the key |
| `X-Umai-Provider-Key` | when the route's `credential` is `pass_through` | The provider credential for this call. Sent upstream as `Authorization: Bearer …` (`openai`, `openai_compatible`) or `api-key` (`azure_openai`). Never stored, logged or echoed (ADR-0035 §4) |
| `X-Umai-Request-Id` | no | Echoed; generated when absent; used as trace id |
| `X-Umai-Session-Id` | no | `subject.session_id` |
| `X-Umai-Subject-User` | no | `subject.user_id`; when absent the body's `safety_identifier`, then the deprecated `user` field |
| `X-Umai-Subject-Role`, `X-Umai-Subject-Department` | no | `subject.role`, `subject.department` |
| `X-Umai-Agent-Id` | no | `subject.agent_id` |

The gateway builds the upstream request headers itself (content type, accept, credential); client
headers are not forwarded in 0.1.

| Response header | Meaning |
|---|---|
| `X-Umai-Request-Id` | The call's id |
| `X-Umai-Event-Id` | Decision event of the input check |
| `X-Umai-Input-Decision` | Action of the input check (`ALLOW`, `ALLOW_WITH_WARNINGS`, `ALLOW_WITH_MODIFICATIONS`) |
| `X-Umai-Output-Decision` | Action of the answer check; non-streaming responses only |
| `X-Umai-Upstream-Error` | `true` when the body is a provider error passed through |

## Routes and `model`

The request's `model` is a **route id** of the caller's tenant. The gateway resolves it, checks
that the route is enabled, and replaces `model` with the route's upstream model before forwarding.
`GET /v1/models` lists the tenant's enabled routes.

A route is tenant configuration in the tenant store (ADR-0034 §6), not policy. Shape:

```yaml
route_id: gpt-eu
tenant_id: tenant-demo
enabled: true
upstream:
  kind: azure_openai                 # openai | azure_openai | openai_compatible
  base_url: https://example-resource.openai.azure.com/openai/v1/
  model: gpt-eu-deployment
  region: eu
credential: pass_through             # pass_through | none
guardrail: gr-demo-data-protection   # bundle id of the same tenant; its active version applies
destination_class: external_llm      # DestinationClass
stream_mode: window                  # buffer | window | after
refusal: error                       # error | message
refusal_text: This request was blocked by your organization's AI policy.
unscanned_parts: reject              # reject | pass_unscanned
timeouts_ms: { connect: 3000, first_byte: 60000, total: 300000 }
```

| Field | Notes |
|---|---|
| `route_id` | `^[a-z0-9][a-z0-9._-]{0,63}$`, unique per tenant; what callers send as `model` |
| `upstream.kind` | Selects the credential header and the provider error shape |
| `upstream.base_url` | HTTPS; must match the deployment's upstream allow-list (settings): no IP literals, no private or link-local addresses unless a self-hosted operator lists the host explicitly |
| `upstream.model` | Sent upstream as `model` (the deployment name for Azure OpenAI) |
| `upstream.region` | Declared processing region; a regional stack refuses routes whose region the tenant's configuration does not allow |
| `credential` | `pass_through`: `X-Umai-Provider-Key` required. `none`: no credential upstream (self-hosted servers). Stored keys: later (Q47) |
| `guardrail` | Bundle evaluated for every check of the call; recorded with id, version and hash on every decision |
| `stream_mode` | See [Streaming](#streaming). Default `window` is proposed (Q50) |
| `refusal`, `refusal_text` | See [Decisions on the wire](#decisions-on-the-wire). Default `error` is proposed (Q48) |
| `unscanned_parts` | What happens to content parts the guardrail cannot scan (image, audio, file): `reject` (400 `unsupported_content`) or `pass_unscanned` (forwarded, the decision is marked `degraded`) |
| `timeouts_ms` | Explicit; `connect`, `first_byte` (until upstream response headers), `total`; capped by settings |

`GET /v1/models` returns the OpenAI list shape:

```json
{
  "object": "list",
  "data": [
    { "id": "gpt-eu", "object": "model", "created": 0, "owned_by": "umai" }
  ]
}
```

## Request fields the gateway reads

| Field | Use |
|---|---|
| `model` | Route id |
| `messages[].role` | `developer` and `system` are evaluated as `system`; `user`, `assistant`; `tool` messages are evaluated separately (below) |
| `messages[].content` | A string, or an array of parts. `text` parts are scanned and may be rewritten in place; other part types follow `unscanned_parts` |
| `messages[].tool_calls[].function.arguments` | Assistant tool calls in the history; scanned with the input check and masked like text |
| `messages[].tool_call_id` | Links a `tool` message to its call, giving `action.tool_name` |
| `stream` | Streaming (see below) |
| `stream_options.include_usage` | When the caller did not set it, the gateway sets it upstream for its usage record and removes the usage-only final chunk before the caller sees it |
| `n` | Each choice is checked on its own |
| `safety_identifier`, `user` | Fallbacks for `subject.user_id` |
| anything else | Forwarded unchanged, never interpreted |

## Checks in one call

| Check | Phase | Content | When |
|---|---|---|---|
| Input | `PRE_LLM` | Every `system`, `developer`, `user` and `assistant` message, including history and tool-call arguments in the history | Before forwarding |
| Tool results | `TOOL_OUTPUT` | Each `tool` message, `action.tool_name` from the matching call | Before forwarding (WP5) |
| Answer | `POST_LLM` | Each choice's `message.content` (stream: the accumulated text) | After the upstream answers |
| Tool calls | `TOOL_INPUT` | Each complete tool call in the answer: `action.name` and `action.tool_name` = `function.name`, content = `function.arguments` | After the call is complete (WP5) |

- The gateway scans **every message it forwards**, whatever the bundle's `l0.target` says (a
  widening only): a value masked in an earlier turn is re-sent by the caller in the history and
  must be masked again.
- The request is forwarded only if every input and tool-result check allows it; the strictest
  action applies. After masking, attack and agent-action checks still run on the masked text
  (ADR-0036).
- **One placeholder namespace per call.** All checks of a call share placeholder numbering: the
  same value gets the same placeholder everywhere in the call, different values never share one.
- The placeholder → value map stays in the gateway's memory for the call only (ADR-0035).

## Decisions on the wire

| Decision | Input / tool results | Answer, non-streaming | Answer, streaming |
|---|---|---|---|
| `ALLOW` | Forward | Return | Stream |
| `ALLOW_WITH_WARNINGS` | Forward; header | Return; header | Stream |
| `ALLOW_WITH_MODIFICATIONS` | Forward the masked messages | Return masked content (entities the model produced stay masked) | Stream masked content |
| `STEP_UP_APPROVAL` | As `BLOCK`, `code: umai_step_up_required` (an API has no approval interface) | As `BLOCK` | As `BLOCK` |
| `BLOCK` | Refuse; nothing reaches the upstream | Refuse per `refusal` | End the stream: final chunk with `finish_reason: "content_filter"` |
| `MONITOR` mode | Forward unchanged; `would_have` recorded | Return unchanged | Stream unchanged |

Placeholders that came from the call's input are **restored** in answer content and, after the
`TOOL_INPUT` check, in tool-call arguments, by exact match only. Placeholders the model altered or
invented stay as they are. Restoration never happens in events, logs or upstream requests.

**`refusal: error`** — HTTP 400 in the OpenAI error shape. Reasons carry entity types and ids,
never values:

```json
{
  "error": {
    "message": "Blocked by UMAI guardrail gr-demo-data-protection: TR_TCKN detected",
    "type": "umai_guardrail",
    "param": null,
    "code": "umai_blocked",
    "umai": {
      "request_id": "req-01J9X7Z8QK",
      "event_id": "ta-01J9X7Z9AB",
      "phase": "PRE_LLM",
      "action": "BLOCK",
      "entities": ["TR_TCKN"],
      "matched": ["identity-data"]
    }
  }
}
```

**`refusal: message`** — HTTP 200 with a completion whose only choice carries `refusal_text` and
`finish_reason: "content_filter"`, for chat interfaces that cannot show errors:

```json
{
  "id": "chatcmpl-umai-req-01J9X7Z8QK",
  "object": "chat.completion",
  "created": 1760870000,
  "model": "gpt-eu",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "This request was blocked by your organization's AI policy.", "refusal": null },
      "finish_reason": "content_filter"
    }
  ],
  "usage": { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0 }
}
```

A blocked non-streaming answer follows the same `refusal` setting (`phase: POST_LLM`); the upstream's
tokens are still recorded as usage.

## Streaming

| `stream_mode` | Behaviour | Guarantee |
|---|---|---|
| `buffer` | The whole answer is collected, checked, then sent as one stream | Same as non-streaming |
| `window` | The gateway holds back the last *k* characters of each choice (settings; at least the longest placeholder plus the longest entity pattern), re-runs L0 over the raw accumulated text and releases what is safe; on `BLOCK` it ends the stream with `finish_reason: "content_filter"` | No detected entity leaves unmasked; decision-model policies on the answer can only record |
| `after` | Unchanged stream; the answer is checked at the end and the decision recorded | Detection only |

- Tool-call deltas are buffered per call (`index`) until the call is complete, then checked
  (`TOOL_INPUT`) and released or the stream ends.
- Restoration holds back an unfinished placeholder until it closes.
- Chunks keep the provider's shape (`chat.completion.chunk`); the gateway only changes
  `delta.content`, `delta.tool_calls[].function.arguments` and `finish_reason`. The stream
  terminator is forwarded as received (OpenAI streams end with `data: [DONE]`; to be confirmed on a
  live stream in WP3).

## Gateway errors

Admission and gateway errors use the OpenAI error shape with `type: "umai_gateway"`:

| HTTP | `code` | When |
|---|---|---|
| 401 | `invalid_credential` | Missing or unknown UMAI key |
| 403 | `insufficient_scope` | The key lacks `gateway` |
| 404 | `unknown_route` | `model` is not an enabled route of the tenant |
| 400 | `provider_key_required` | The route is `pass_through` and `X-Umai-Provider-Key` is missing |
| 400 | `unsupported_content` | A content part cannot be scanned and the route says `reject` |
| 413 | `input_too_large` | Content above the size limit ([evaluation API](evaluation-api.md) 0.9 semantics) |
| 429 | `rate_limited`, `quota_exceeded` | Admission limits, with `Retry-After` |
| 502 | `upstream_unreachable` | Connection to the upstream failed |
| 504 | `upstream_timeout` | A route timeout expired |
| upstream's | (provider body unchanged) | The provider answered with an error; `X-Umai-Upstream-Error: true` |

A guardrail failure is not an error here: the bundle's failure policy produces a decision
(`fail_closed` → `BLOCK` with reason `EVALUATION_UNAVAILABLE`), returned as above.

## Records

Every check produces a decision event ([tagged action](tagged-action.md); `route_id` and
`source: gateway` are added with WP2) and every call one usage record: route id, upstream kind and
model, provider-reported token counts, latency per stage (admission, input check, upstream first
byte, upstream total, answer check), status. No record carries prompt, answer, provider key or
mask map (ADR-0035).

## Changelog

- 0.1 (2026-10-19): initial draft: Chat Completions surface, routes, headers, checks per call,
  decisions on the wire, streaming modes, gateway errors, records (ADR-0034).
