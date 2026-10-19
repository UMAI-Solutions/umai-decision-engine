# Contract: gateway API

**Version:** 0.4 · **Status:** implemented for Chat Completions and the Responses API,
non-streaming and streaming: routes, input, tool-result, answer and tool-call checks, masking,
restoration and refusals (WP2, WP3, WP5; [design](../architecture/ai-gateway.md) §8) · **Endpoints**
(gateway host, e.g. `eu.gateway.<domain>`): `POST /v1/chat/completions`, `POST /v1/responses`, `GET /v1/models` ·
**Health:** `GET /healthz`, `GET /readyz` · **Code:** `src/umai_decision_engine/gateway/`,
models in `contracts/gateway.py` · **Decisions:** [ADR-0034](../adr/0034-umai-secure-gateway.md),
[ADR-0035](../adr/0035-raw-content-zone-l0-and-gateway.md),
[ADR-0036](../adr/0036-checks-continue-after-masking.md)

The gateway speaks the provider's wire format, OpenAI Chat Completions. It reads and may rewrite
only the fields this document names; every other request and response field passes through
unchanged. Provider shapes were checked on 2026-10-19 against OpenAI's
[Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create),
[streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
and [error codes](https://developers.openai.com/api/docs/guides/error-codes); re-verify before
changing an implementation (AGENTS rule 7). The official `openai` Python SDK, pointed at the
gateway with `base_url`, is part of the test suite.

## Headers

| Request header | Required | Meaning |
|---|---|---|
| `Authorization: Bearer <UMAI key>` | yes | Per-tenant API key with scope `gateway` ([credentials](credentials.md) 0.4). The tenant comes from the key. The shared operator token (self-hosted, ADR-0016) and the open development mode act as the deployment's default tenant |
| `X-Umai-Provider-Key` | when the route's `credential` is `pass_through` | The provider credential for this call. Sent upstream as `Authorization: Bearer …` (`openai`, `openai_compatible`) or `api-key` (`azure_openai`). Never stored, logged or echoed (ADR-0035 §4) |
| `X-Umai-Request-Id` | no | Echoed; generated when absent; used as trace id |
| `X-Umai-Session-Id` | no | `subject.session_id` |
| `X-Umai-Subject-User` | no | `subject.user_id`; when absent the body's `safety_identifier`, then the deprecated `user` field |
| `X-Umai-Subject-Role`, `X-Umai-Subject-Department` | no | `subject.role`, `subject.department` |
| `X-Umai-Agent-Id` | no | `subject.agent_id` |

With the OpenAI SDK: `OpenAI(base_url="https://<gateway>/v1", api_key="<UMAI key>")`, plus
`default_headers={"X-Umai-Provider-Key": "<provider key>"}` for `pass_through` routes. The gateway
builds the upstream request headers itself (content type, accept, credential); client headers are
not forwarded.

| Response header | Meaning |
|---|---|
| `X-Umai-Request-Id` | The call's id |
| `X-Umai-Event-Id` | Decision event of the input check |
| `X-Umai-Input-Decision` | Action of the input check |
| `X-Umai-Output-Decision` | Strictest action of the answer checks; non-streaming answers and `buffer` streams only |
| `X-Umai-Unscanned-Parts` | Number of content parts forwarded unscanned (`unscanned_parts: pass_unscanned`) |
| `X-Umai-Upstream-Error` | `true` when the body is a provider error passed through |
| `x-request-id`, `openai-processing-ms`, `openai-version`, `retry-after`, `apim-request-id`, `x-ratelimit-*` | Passed on from the upstream |

## Routes and `model`

The request's `model` is a **route id** of the caller's tenant. The gateway resolves it, checks
that the route is enabled, and replaces `model` with the route's upstream model before forwarding.
`GET /v1/models` lists the tenant's enabled routes.

A route is tenant configuration, not policy (ADR-0034 §6). Routes live in one YAML file per tenant,
`<UMAI_DE_GATEWAY_ROUTES_DIR>/<tenant_id>.yaml` (`routes: [ … ]`), hot-reloaded; a file that fails
validation keeps the tenant's last good routes and is reported by file name. (Editing routes in the
console through the tenant store is WP7.) One route:

```yaml
route_id: gpt-eu
enabled: true
upstream:
  kind: azure_openai                 # openai | azure_openai | openai_compatible
  base_url: https://example-resource.openai.azure.com/openai/v1/
  model: gpt-eu-deployment
  region: eu
credential: secret                   # pass_through | secret | none
secret: azure-eu                     # credential: secret → key name in UMAI_DE_GATEWAY_PROVIDER_KEYS
guardrail: gr-demo-data-protection   # bundle id of the same tenant; its active version applies
destination_class: external_llm      # DestinationClass
stream_mode: window                  # buffer | window | after
refusal: error                       # error | message
refusal_text: This request was blocked by your organization's AI policy.
unscanned_parts: reject              # reject | pass_unscanned
restore_placeholders: true
timeouts_ms: { connect: 5000, first_byte: 120000, total: 600000 }
```

| Field | Notes |
|---|---|
| `route_id` | `^[a-z0-9][a-z0-9._-]{0,63}$`, unique per tenant; what callers send as `model` |
| `upstream.kind` | Selects the credential header |
| `upstream.base_url` | Must match an entry of the deployment's allow-list `UMAI_DE_GATEWAY_UPSTREAM_ALLOWLIST` (`scheme://host-glob[:port]`; default: OpenAI and Azure OpenAI / Foundry hosts over https). An IP-literal host matches only an entry that names it exactly. A route outside the list does not load |
| `upstream.model` | Sent upstream as `model` (the deployment name for Azure OpenAI) |
| `upstream.region` | Declared processing region (recorded; region enforcement per tenant comes with the regional stack) |
| `credential` | `pass_through`: `X-Umai-Provider-Key` required on every call. `secret`: a provider key the deployment holds in settings (`UMAI_DE_GATEWAY_PROVIDER_KEYS`, name → key), so callers need only their UMAI key. `none`: no credential (self-hosted servers). Tenant-managed stored keys: later (Q47) |
| `guardrail` | Bundle evaluated for every check of the call; recorded with id, version and hash on every decision. A phase the bundle does not enable is not checked for this route (configuration, not a failure); a missing bundle blocks (`CONFIG_MISSING`) |
| `stream_mode` | See [Streaming](#streaming). Default `window` (Q50) |
| `refusal`, `refusal_text` | See [Decisions on the wire](#decisions-on-the-wire). Default `error` (Q48) |
| `unscanned_parts` | Content parts the guardrail cannot scan (image, audio, file): `reject` (400 `unsupported_content`) or `pass_unscanned` (forwarded, counted in `X-Umai-Unscanned-Parts`) |
| `restore_placeholders` | `true`: the caller's own masked values are put back into the answer (below) |
| `timeouts_ms` | `connect`; `first_byte` (longest wait for upstream data, also between stream chunks); `total` (whole call) |

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
| `messages[].role` | `system`, `developer` → evaluated as `system`; `user`; `assistant`; `tool` and the deprecated `function` → evaluated as input from the caller |
| `messages[].content` | A string, or an array of parts: `text` (and `refusal`) parts are scanned and may be rewritten in place; other part types follow `unscanned_parts` |
| `messages[].tool_calls[].function.arguments`, `messages[].function_call.arguments` | Assistant tool calls in the history; scanned with the input and masked like text |
| `stream` | `true`: streaming (below) |
| `stream_options.include_usage` | For `openai` and `azure_openai` upstreams the gateway sets it for its usage record and removes the usage-only chunk when the caller did not ask for it |
| `n` | Each choice is checked on its own |
| `safety_identifier`, `user` | Fallbacks for `subject.user_id` |
| anything else | Forwarded unchanged, never interpreted |

Limits: the body is at most `UMAI_DE_GATEWAY_MAX_REQUEST_BYTES` (413 `request_too_large`); the
scanned text at most `UMAI_DE_MAX_INPUT_CHARS` characters (413 `input_too_large`).

## Checks in one call

| Check | Phase | Content | When |
|---|---|---|---|
| Input | `PRE_LLM` | Every message the gateway forwards: system, developer, user, assistant (with tool-call arguments), history included; tool results too when the bundle does not enable `TOOL_OUTPUT` | Before forwarding |
| Tool results | `TOOL_OUTPUT` | Each `tool` / `function` message on its own, `action.name` = `action.tool_name` = the tool's name (from the matching `tool_call_id`), so policies with the `tool` role and exceptions on the tool name apply | Before forwarding, concurrently with the input check, when the bundle enables `TOOL_OUTPUT` |
| Answer | `POST_LLM` | Each choice's `message.content` (streams: per release step and on the whole answer) | After the upstream answers |
| Tool calls | `TOOL_INPUT` | Each complete tool call in the answer, after restoration: `action.name` = `action.tool_name` = `function.name`, content = `function.arguments` (policies with the `tool_call` role) | When the bundle enables `TOOL_INPUT`; masked arguments keep their placeholders |

- The gateway scans **every message it forwards**, whatever the bundle's `l0.target` says (a
  widening only): a value masked in an earlier turn is re-sent by the caller in the history and
  must be masked again.
- The request is forwarded only if every input and tool-result check allows it; the strictest
  action applies and names the phase that refused (`umai.phase`).
  After masking, attack and agent-action checks still run on the masked text (ADR-0036).
- **One placeholder namespace per call.** All checks of a call share placeholder numbering: the
  same value (same type and tenant HMAC) gets the same placeholder everywhere in the call,
  different values never share one.
- The placeholder → value map stays in the gateway's memory for the call only (ADR-0035).

## Decisions on the wire

| Decision | Input | Answer, non-streaming | Answer, streaming |
|---|---|---|---|
| `ALLOW` | Forward | Return | Stream |
| `ALLOW_WITH_WARNINGS` | Forward | Return | Stream |
| `ALLOW_WITH_MODIFICATIONS` | Forward the masked messages | Return masked content (entities the model produced stay masked) | Stream masked content |
| `STEP_UP_APPROVAL` | As `BLOCK`, `code: umai_step_up_required` (an API has no approval interface) | As `BLOCK` | As `BLOCK` |
| `BLOCK` | Refuse; nothing reaches the upstream | Refuse per `refusal` | `window`: end the choice with `finish_reason: "content_filter"`; `buffer`: refuse per `refusal` |
| `MONITOR` mode | Forward unchanged; `would_have` recorded | Return unchanged | Stream unchanged |

**Restoration.** After the answer check, placeholders that came from the call's input are put back
in the answer content and in tool-call arguments (JSON-escaped), by exact match only. Output
policies apply to what the model produced, not to the caller's own values echoed back to the same
caller; a route that must never show them back sets `restore_placeholders: false`. Placeholders the
model altered or invented stay as they are. Restoration never happens in events, logs or upstream
requests.

**`refusal: error`** — HTTP 400 in the OpenAI error shape (the OpenAI SDK raises
`BadRequestError` with this `code`). Reasons carry entity types and ids, never values:

```json
{
  "error": {
    "message": "Blocked by UMAI guardrail gr-demo-data-protection: L0: CREDIT_CARD detected by policy payment",
    "type": "umai_guardrail",
    "code": "umai_blocked",
    "umai": {
      "request_id": "gw-3f9c1e0b",
      "event_id": "ta-01J9X7Z9AB",
      "phase": "PRE_LLM",
      "action": "BLOCK",
      "entities": ["CREDIT_CARD"],
      "matched": ["payment"]
    }
  }
}
```

**`refusal: message`** — HTTP 200 with a completion whose only choice carries `refusal_text` and
`finish_reason: "content_filter"` (streaming requests: the same as two chunks), for chat interfaces
that cannot show errors:

```json
{
  "id": "chatcmpl-umai-gw-3f9c1e0b",
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

A blocked non-streaming answer or tool call follows the same `refusal` setting (`phase:
POST_LLM` or `TOOL_INPUT`); with `message`, only the blocked choice is replaced and its tool calls
are dropped. In `window` streams a blocked tool call is never released: the choice ends with
`finish_reason: "content_filter"`.

## Streaming

| `stream_mode` | Behaviour | Guarantee |
|---|---|---|
| `buffer` | The whole upstream stream is read and checked before the response starts, then replayed as a stream | Same as non-streaming, including an HTTP 400 refusal |
| `window` | Per choice the gateway holds back the last `UMAI_DE_GATEWAY_STREAM_HOLDBACK_CHARS` characters (default 128), re-checks the raw tail every `UMAI_DE_GATEWAY_STREAM_STEP_CHARS` (default 32) and releases what is safe; an entity or a placeholder straddling the boundary is held until complete; the whole answer is checked before the last characters go out; on `BLOCK` the choice ends with `finish_reason: "content_filter"` and the upstream is closed | No detected entity leaves unmasked; decision-model policies on the answer act per release step |
| `after` | Unchanged stream (placeholders still restored); the whole answer is checked at the end and the decision recorded | Detection only |

- Tool-call deltas are buffered per call; when the choice finishes each call is restored, checked
  (`TOOL_INPUT`) and released complete, or the choice ends with `content_filter`.
- Chunks keep the provider's shape (`chat.completion.chunk`, upstream `id`, `model`, `created`);
  the gateway writes only `delta.content`, `delta.tool_calls` and `finish_reason`; other delta
  fields (role, model refusal text) pass through. The stream ends with `data: [DONE]`.
- A provider error event mid-stream passes through; an upstream that stops answering gets an
  `{"error": {"code": "upstream_timeout", …}}` event before `[DONE]`.

## OpenAI Responses API (`POST /v1/responses`, 0.4)

The same routes, headers, checks, masking, restoration and refusals as Chat Completions, in the
Responses wire format (shapes recorded live against OpenAI on 2026-10-19). Upstream path:
`<base_url>/responses` (OpenAI; Azure OpenAI v1 `…/openai/v1/responses`; OpenAI-compatible servers
that serve Responses).

| Request field | Use |
|---|---|
| `instructions` | Scanned as a system message; the echo in the response object is restored |
| `input` (string) | Scanned as a user message |
| `input[]` `message` (or `{role, content}`) | `input_text` / `output_text` / `refusal` parts scanned; `input_image`, `input_file`, `input_audio` follow `unscanned_parts` |
| `input[]` `function_call`, `custom_tool_call` | `arguments` / `input` scanned with the input check |
| `input[]` `function_call_output`, `custom_tool_call_output` | `output` (string or `input_text` parts) checked as a tool result (`TOOL_OUTPUT` with the tool's name from `call_id`, else part of the input check) |
| `input[]` `reasoning`, `item_reference`, hosted tool calls (`web_search_call`, `file_search_call`, `code_interpreter_call`, `image_generation_call`, `mcp_list_tools`, compaction) | Provider-side items: passed through, nothing to scan |
| `input[]` any other item (shell, computer use, apply-patch outputs, MCP calls) | Not scanned: follows `unscanned_parts` |
| everything else (`tools`, `previous_response_id`, `store`, `reasoning`, `text`, …) | Forwarded unchanged |

Answer: each `output_text` part of a `message` item is checked (`POST_LLM`), masked and restored;
each `function_call` item's `arguments` are restored, then checked (`TOOL_INPUT`). A refusal with
`refusal: error` is HTTP 400 as above; with `refusal: message` the response has `status:
"incomplete"`, `incomplete_details: {"reason": "content_filter"}` and one `message` item carrying
`refusal_text` (the SDK's `output_text`).

Streaming uses the Responses events (`event: <type>` + `data: {…}`; the stream ends with
`response.completed` or `response.incomplete`, there is no `[DONE]`):

- `response.output_text.delta` events are held back and released as in `window` / `buffer` /
  `after`; `response.output_text.done`, `response.content_part.done`, `response.output_item.done`
  and `response.completed` carry the checked, restored text.
- `response.function_call_arguments.delta` events are held; at `…arguments.done` the call is
  restored and checked, then released as one delta plus the done event.
- A block ends the stream with `response.incomplete` (`incomplete_details.reason:
  "content_filter"`) and closes the upstream; in `buffer` mode with `refusal: error` the answer is
  HTTP 400 instead.
- The gateway re-numbers `sequence_number` (it holds and rewrites events) and drops `obfuscation`
  padding from the events it rewrites.

Not checked by the gateway: what hosted tools (web search, file search, code interpreter, remote
MCP) read and return inside the provider, and items stored at the provider and referenced by
`previous_response_id` or `item_reference` (they were checked when they passed through).

## Gateway errors

Admission and gateway errors use the OpenAI error shape with `type: "umai_gateway"`:

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Body is not JSON, not an object, has no `model` or unusable `messages` (the message names the field, never the content) |
| 400 | `provider_key_required` | The route is `pass_through` and `X-Umai-Provider-Key` is missing |
| 400 | `unsupported_content` | A content part cannot be scanned and the route says `reject` |
| 401 | `invalid_credential` | Missing or unknown UMAI key |
| 403 | `insufficient_scope` | The key lacks `gateway` |
| 404 | `unknown_route` | `model` is not an enabled route of the tenant |
| 413 | `request_too_large`, `input_too_large` | Body bytes or scanned characters above the limit |
| 429 | `rate_limited` | Admission limit per key, with `Retry-After` |
| 500 | `route_misconfigured` | A `secret` route whose key the deployment does not hold |
| 502 | `upstream_unreachable`, `upstream_invalid_response` | Connection failed, or a non-JSON answer |
| 504 | `upstream_timeout` | A route timeout expired |
| upstream's | (provider body unchanged) | The provider answered with an error; `X-Umai-Upstream-Error: true` |

A guardrail failure is not an error here: the bundle's failure policy produces a decision
(`fail_closed` → `BLOCK` with reason `EVALUATION_UNAVAILABLE`), returned as above.

## Records

Every check is an evaluation with its own `event_id` (decision events are stored once the event
sink exists, WP6). Every call writes one log record: request id, tenant, route, upstream kind and
model, stream, status, input and output decisions, refusal, provider-reported token counts and total
latency. No record carries prompt, answer, provider key or mask map (ADR-0035).

## Changelog

- 0.4 (2026-10-19): OpenAI Responses API (`POST /v1/responses`), non-streaming and
  streaming, with the same checks; shapes recorded live; refusals as `status: incomplete`
  with `incomplete_details.reason: content_filter` or HTTP 400.
- 0.3 (2026-10-19): agents (WP5): tool results checked as `TOOL_OUTPUT` per message with the
  tool's name when the bundle enables it (else part of the input check); tool calls in answers
  checked as `TOOL_INPUT` after restoration, masked in place or refused (`phase: TOOL_INPUT`).
- 0.2 (2026-10-19): implemented (WP2, WP3). Routes as one YAML file per tenant, hot-reloaded;
  `credential: secret` (deployment-held provider keys, `secret` names the key) and
  `restore_placeholders`; tool results are part of the input check until WP5 adds `TOOL_OUTPUT`;
  restoration after the answer check; `buffer` streams can refuse with HTTP 400; errors
  `invalid_request`, `request_too_large`, `route_misconfigured`, `upstream_invalid_response`;
  response header `X-Umai-Unscanned-Parts`; records are log lines until the event sink exists.
- 0.1 (2026-10-19): initial draft: Chat Completions surface, routes, headers, checks per call,
  decisions on the wire, streaming modes, gateway errors, records (ADR-0034).
