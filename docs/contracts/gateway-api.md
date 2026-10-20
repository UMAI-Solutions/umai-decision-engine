# Contract: gateway API

**Version:** 0.10 · **Status:** implemented for Chat Completions and the Responses API,
non-streaming and streaming, and for embeddings (input checks): applications, providers, input, tool-result, answer and tool-call checks, masking,
restoration and refusals (WP2, WP3, WP5; [design](../architecture/ai-gateway.md) §8) · **Endpoints**
(gateway host, e.g. `eu.gateway.<domain>`): `POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/embeddings`,
`GET /v1/models` ·
**Health:** `GET /healthz`, `GET /readyz` · **Code:** `src/umai_decision_engine/gateway/`,
models in `contracts/gateway.py` · **Decisions:** [ADR-0034](../adr/0034-umai-secure-gateway.md),
[ADR-0035](../adr/0035-raw-content-zone-l0-and-gateway.md),
[ADR-0036](../adr/0036-checks-continue-after-masking.md),
[ADR-0037](../adr/0037-per-message-l0-scanning-and-cache.md),
[ADR-0038](../adr/0038-gateways-instead-of-model-routes.md),
[ADR-0039](../adr/0039-applications-and-connections.md)

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
| `Authorization: Bearer <UMAI key>` | yes | Per-tenant API key with scope `gateway` ([credentials](credentials.md) 0.6). The tenant, and the application when the key belongs to one, come from the key. The shared operator token (self-hosted, ADR-0016) and the open development mode act as the deployment's default tenant |
| `X-Umai-Application` | when the key belongs to no application and the tenant has several | The application's id ([Applications](#applications-providers-and-model-010-adr-0039)) |
| `X-Umai-Provider-Key` | when the target provider's `credential` is `pass_through` | The provider credential for this call. Sent upstream as `Authorization: Bearer …` (`openai`, `openai_compatible`) or `api-key` (`azure_openai`). Never stored, logged or echoed (ADR-0035 §4) |
| `X-Umai-Request-Id` | no | Echoed; generated when absent; used as trace id |
| `X-Umai-Session-Id` | no | `subject.session_id` |
| `X-Umai-Subject-User` | no | `subject.user_id`; when absent the body's `safety_identifier`, then the deprecated `user` field |
| `X-Umai-Subject-Role`, `X-Umai-Subject-Department` | no | `subject.role`, `subject.department` |
| `X-Umai-Agent-Id` | no | `subject.agent_id` |

With the OpenAI SDK: `OpenAI(base_url="https://<gateway>/v1", api_key="<UMAI key>")`, plus
`default_headers={"X-Umai-Provider-Key": "<provider key>"}` for `pass_through` providers. The gateway
builds the upstream request headers itself (content type, accept, credential); client headers are
not forwarded.

| Response header | Meaning |
|---|---|
| `X-Umai-Request-Id` | The call's id |
| `X-Umai-Application` | The application the call was made for |
| `X-Umai-Event-Id` | Decision event of the input check |
| `X-Umai-Input-Decision` | Action of the input check |
| `X-Umai-Output-Decision` | Strictest action of the answer checks; non-streaming answers and `buffer` streams only |
| `X-Umai-Unscanned-Parts` | Number of content parts forwarded unscanned (`unscanned_parts: pass_unscanned`) |
| `X-Umai-Upstream-Error` | `true` when the body is a provider error passed through |
| `X-Umai-Fallback` | The application whose fallback target answered (below) |
| `x-request-id`, `openai-processing-ms`, `openai-version`, `retry-after`, `apim-request-id`, `x-ratelimit-*` | Passed on from the upstream |

## Applications, providers and `model` (0.10, ADR-0039)

An **application** is what UMAI protects: one guardrail, its keys, and options for the gateway.
The gateway sends its calls to **providers**, connected once per organization. The `model` the
application sends picks the provider:

1. **Selecting the application:**
   1. A key that belongs to an application (`application_id`, [credentials](credentials.md) 0.6)
      uses that application. An `X-Umai-Application` header naming another one is refused (403
      `application_mismatch`).
   2. The shared operator token, the open development mode and keys without an application use
      the `X-Umai-Application` header.
   3. Without the header, they use the organization's only enabled application.
   4. With several applications and no header, the call is refused (400 `application_required`).

   A key whose application is missing or disabled is refused (404 `unknown_application`). It
   never falls through to another application.
2. **Resolving `model`:**
   1. When the application has a `models` list, only those names and its alias names are
      accepted (404 `model_not_allowed`).
   2. A name listed in the application's `aliases` is replaced by the alias's target.
   3. A target `@<provider>/<model>` goes to that provider with `<model>`; an unknown or disabled
      provider is refused (404 `unknown_provider`).
   4. Any other target goes to the organization's **default provider** unchanged.
   5. When the application has a `providers` list, a provider outside it is refused (403
      `provider_not_allowed`).
   6. A name must match `^(@[a-z0-9][a-z0-9-]{0,63}/)?[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$` (400
      `invalid_request`), because it is logged.
3. **`GET /v1/models`:**
   - An application with `models` or `aliases` lists those names.
   - Otherwise the request goes to its provider's `<base_url>/models` with that provider's
     credential (the default provider, or the application's first allowed one), and the
     provider's list is passed through.

**The built-in OpenAI provider.** Every organization has a provider `openai` (`https://api.openai.com/v1`,
the caller's own key) without any setup, and it is the default until another provider is marked
`default`. Defining a provider with the id `openai` replaces it, for example to disable it.

The applications, the providers and the gateway's defaults are tenant configuration, not policy
(ADR-0034 §6, ADR-0039). They live in one YAML file per tenant,
`<UMAI_DE_APPLICATIONS_DIR>/<tenant_id>.yaml`, read by the engine and the gateway and
hot-reloaded. A file that fails validation keeps the tenant's last good content and is reported
by file name. The policy service writes these files for the console (below). Example:

```yaml
gateway:                             # defaults for every application on the gateway
  stream_mode: window                # buffer | window | after
  refusal: error                     # error | message
  refusal_text: This request was blocked by your organization's AI policy.
  unscanned_parts: reject            # reject | pass_unscanned
  restore_placeholders: true
  timeouts_ms: { connect: 5000, first_byte: 120000, total: 600000 }
providers:
  - provider_id: contoso-hr          # also the model prefix: @contoso-hr/<deployment>
    name: Azure OpenAI (HR)
    kind: azure_openai               # openai | azure_openai | openai_compatible
    base_url: https://example-resource.openai.azure.com/openai/v1/
    region: eu
    credential: secret               # pass_through | secret | none
    secret: azure-eu                 # credential: secret → key name in UMAI_DE_GATEWAY_PROVIDER_KEYS
    default: false
applications:
  - application_id: hr-assistant
    name: HR assistant
    enabled: true
    guardrail: gr-demo-data-protection   # bundle id of the same tenant; its active version applies
    providers: [contoso-hr]          # optional: providers it may use; empty: any
    models: []                       # optional: names it may send; empty: any
    aliases:                         # optional: name it sends → target
      - { name: gpt-4.1-mini, model: "@contoso-hr/hr-gpt41-deployment" }
    fallback: "@contoso-hr/hr-gpt41-backup"   # optional: tried once on 429 / 5xx / unreachable
    refusal: message                 # optional overrides of the gateway defaults
```

| Field | Notes |
|---|---|
| `gateway.*` | Defaults for every application: streaming ([Streaming](#streaming), default `window`, Q50), refusal style and text ([Decisions on the wire](#decisions-on-the-wire), default `error`, Q48), unscanned parts, restoration, timeouts. An application overrides any of them |
| `providers[].provider_id` | `^[a-z0-9][a-z0-9-]{0,63}$`, unique; the `@<provider>/` prefix of model names |
| `providers[].kind` | Selects the credential header |
| `providers[].base_url` | Defaults to `https://api.openai.com/v1` for `openai`; required for the other kinds. It must match an entry of the deployment's allow-list `UMAI_DE_GATEWAY_UPSTREAM_ALLOWLIST` (`scheme://host-glob[:port]`; default: OpenAI and Azure OpenAI / Foundry hosts over https). An IP-literal host matches only an entry that names it exactly. A file with a provider outside the list does not load |
| `providers[].region` | Declared processing region (recorded; enforcement per tenant comes with the regional stack) |
| `providers[].credential` | `pass_through`: `X-Umai-Provider-Key` is required on every call. `secret`: a provider key the deployment holds (`UMAI_DE_GATEWAY_PROVIDER_KEYS`, name → key). `none`: no credential (self-hosted servers). Tenant-managed stored keys come later (Q47) |
| `providers[].default` | At most one; bare model names go there. Without one, the built-in `openai` provider is the default; a file whose default is disabled does not load |
| `applications[].application_id` | `^[a-z0-9][a-z0-9-]{0,63}$`, unique per tenant; named by keys and `X-Umai-Application` |
| `applications[].guardrail` | The bundle evaluated for every check of every call of the application, on the gateway and on the evaluation API; recorded with id, version and hash on every decision. A phase the bundle does not enable is not checked (configuration, not a failure). A missing bundle blocks (`CONFIG_MISSING`) |
| `applications[].providers`, `models`, `aliases` | Gateway options; see the resolution above. Alias names follow the model-name pattern; targets may carry an `@<provider>/` prefix |
| `applications[].fallback` | A target tried **once** when the first provider is unreachable, times out or answers 429 / 500 / 502 / 503 / 504 before any answer was sent. The same masked body goes there; answer checks are unchanged. Its provider must be allowed for the application and use credential `secret` or `none` (the caller's pass-through key belongs to the first provider). A file breaking these rules does not load |

`GET /v1/models` with a `models` or `aliases` list returns the OpenAI list shape:

```json
{
  "object": "list",
  "data": [
    { "id": "gpt-4.1-mini", "object": "model", "created": 0, "owned_by": "umai" }
  ]
}
```

### Managing applications and providers (policy service, 0.10)

The policy service (ADR-0022) reads and changes the tenant's file, so the console can manage it;
the engine and the gateway pick it up at their next reload. The tenant is resolved as for every
policy-service route: the person's tenant, or `X-Umai-Tenant` with the control token.

| Route | Action (ADR-0029) | Roles |
|---|---|---|
| `GET /v1/applications` → `{"tenant_id", "applications": [...]}` | `application.read` | owner, admin, policy editor, approver, viewer; operator |
| `PUT /v1/applications/{application_id}` with one application → `{"tenant_id", "application"}` (201 when created) | `application.write` | owner, admin; operator |
| `DELETE /v1/applications/{application_id}` → `{"tenant_id", "deleted"}` | `application.write` | owner, admin; operator |
| `GET /v1/providers` → `{"tenant_id", "providers": [...]}`; each with `default` and `builtin` | `gateway.read` | owner, admin, policy editor, approver, viewer; operator |
| `PUT /v1/providers/{provider_id}` → `{"tenant_id", "provider"}` (201 when created); `default: true` moves the default | `gateway.write` | owner, admin; operator |
| `DELETE /v1/providers/{provider_id}` → `{"tenant_id", "deleted"}` | `gateway.write` | owner, admin; operator |
| `GET` / `PUT /v1/gateway/settings` → `{"tenant_id", "gateway": {...}}` | `gateway.read` / `gateway.write` | as above |

- **`PUT`** validates the tenant's whole file as the engine loads it (schema, references,
  fallback rules, allow-list); for an application also: its `guardrail` is a guardrail of this
  tenant. The file is then written atomically.
- **`DELETE`** is refused while an active key belongs to the application, or while an
  application names the provider (allowed providers, alias or fallback target). The built-in
  `openai` provider can be disabled, not deleted.
- **Refusals:**
  - HTTP 422 `{"detail": {"error": "invalid_configuration" | "unknown_guardrail", "detail":
    "<field: problem>"}}`;
  - HTTP 404 `unknown_application` / `unknown_provider`;
  - HTTP 409 `application_in_use` / `provider_in_use`.

  Nothing is written when a call is refused.
- A key is created for an application with `POST /v1/tenants/{id}/keys` and `application_id`
  ([credentials](credentials.md) 0.6); it works on every connection of the application.

## Request fields the gateway reads

| Field | Use |
|---|---|
| `model` | Resolved by the application's aliases and the provider prefix ([Applications](#applications-providers-and-model-010-adr-0039)) |
| `messages[].role` | `system`, `developer` → evaluated as `system`; `user`; `assistant`; `tool` and the deprecated `function` → evaluated as input from the caller |
| `messages[].content` | A string, or an array of parts: `text` (and `refusal`) parts are scanned and may be rewritten in place; other part types follow `unscanned_parts` |
| `messages[].tool_calls[].function.arguments`, `messages[].function_call.arguments` | Assistant tool calls in the history; scanned with the input and masked like text |
| `stream` | `true`: streaming (below) |
| `stream_options.include_usage` | For `openai` and `azure_openai` upstreams the gateway sets it for its usage record and removes the usage-only chunk when the caller did not ask for it |
| `n` | Each choice is checked on its own |
| `safety_identifier`, `user` | Fallbacks for `subject.user_id` |
| anything else | Forwarded unchanged, never interpreted |

Limits: the body is at most `UMAI_DE_GATEWAY_MAX_REQUEST_BYTES` (413 `request_too_large`); the
scanned text, which is the whole conversation resent on every turn, at most
`UMAI_DE_GATEWAY_MAX_INPUT_CHARS` characters (default 256 000, about 64 000 tokens; 413
`input_too_large`). The engine API's `UMAI_DE_MAX_INPUT_CHARS` (32 000) does not apply here. L0
scans each message on its own and does not scan a message it has seen before, so a turn costs
its new messages (ADR-0037).

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
caller; an application that must never show them back sets `restore_placeholders: false`. Placeholders the
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

The same applications, headers, checks, masking, restoration and refusals as Chat Completions, in the
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

## Embeddings (`POST /v1/embeddings`, 0.5)

Input checks only: each string of `input` (a string or an array of strings) is checked as
`PRE_LLM` and masked before it is embedded, so a retrieval index never stores vectors of the
masked values. Token arrays cannot be scanned and follow `unscanned_parts`. A refusal is always
HTTP 400 `umai_blocked` (there is no text answer to carry a message). The response passes
through unchanged; upstream path `<base_url>/embeddings`.

## Gateway errors

Admission and gateway errors use the OpenAI error shape with `type: "umai_gateway"`:

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Body is not JSON, not an object, has no `model` or unusable `messages` (the message names the field, never the content) |
| 400 | `application_required` | The key belongs to no application, the tenant has several and `X-Umai-Application` is missing |
| 400 | `provider_key_required` | The target provider is `pass_through` and `X-Umai-Provider-Key` is missing |
| 400 | `unsupported_content` | A content part cannot be scanned and the application says `reject` |
| 401 | `invalid_credential` | Missing or unknown UMAI key |
| 403 | `insufficient_scope` | The key lacks `gateway` |
| 403 | `application_mismatch` | `X-Umai-Application` names another application than the key's |
| 403 | `provider_not_allowed` | The application has a `providers` list without the target provider |
| 404 | `unknown_application` | The key's application, or the one named by `X-Umai-Application`, is not an enabled application of the tenant; or the tenant has none |
| 404 | `unknown_provider` | The `@<provider>/` prefix names no enabled provider, or there is no default provider |
| 404 | `model_not_allowed` | The application has a `models` list and `model` is neither in it nor an alias |
| 413 | `request_too_large`, `input_too_large` | Body bytes or scanned characters above the limit |
| 429 | `rate_limited` | Admission limit per key, with `Retry-After` |
| 500 | `provider_misconfigured` | A `secret` provider whose key the deployment does not hold |
| 502 | `upstream_unreachable`, `upstream_invalid_response` | Connection failed, or a non-JSON answer |
| 504 | `upstream_timeout` | A gateway timeout expired |
| upstream's | (provider body unchanged) | The provider answered with an error; `X-Umai-Upstream-Error: true` |

A guardrail failure is not an error here: the bundle's failure policy produces a decision
(`fail_closed` → `BLOCK` with reason `EVALUATION_UNAVAILABLE`), returned as above.

## Records

Every check is an evaluation with its own `event_id` (decision events are stored once the event
sink exists, WP6). Every call writes one log record: request id, tenant, application, upstream kind and
model, stream, status, input and output decisions, refusal, provider-reported token counts and total
latency. No record carries prompt, answer, provider key or mask map (ADR-0035).

## Changelog

- 0.10 (2026-10-20, breaking): applications and providers replace gateways (ADR-0039). The
  application carries the guardrail, keys and gateway options; providers are connected once per
  organization, with a built-in `openai` provider as the default; `@<provider>/<model>` picks
  another provider; gateway defaults with per-application overrides; the fallback is a target;
  `X-Umai-Application`; errors `application_required`, `application_mismatch`,
  `unknown_application`, `unknown_provider`, `provider_not_allowed`, `provider_misconfigured`;
  `/v1/applications`, `/v1/providers`, `/v1/gateway/settings` (actions `application.*`,
  `gateway.*`) replace `/v1/gateways`; `UMAI_DE_APPLICATIONS_DIR` replaces
  `UMAI_DE_GATEWAYS_DIR`.
- 0.9 (2026-10-20, breaking): gateways replace per-model routes (ADR-0038). A gateway is a provider
  connection plus a guardrail; keys bind to a gateway (`X-Umai-Gateway` otherwise, or the only
  gateway); `model` passes through, with optional `aliases` and a `models` allow-list; `GET
  /v1/models` forwards to the provider without a list; fallback names a gateway; errors
  `gateway_required`, `gateway_mismatch`, `unknown_gateway`, `model_not_allowed`,
  `gateway_misconfigured` replace `unknown_route` and `route_misconfigured`; response header
  `X-Umai-Gateway`; `/v1/gateways` (`GET`, `PUT`, `DELETE`, actions `gateway.read` /
  `gateway.write`) replaces `/v1/gateway/routes`; `UMAI_DE_GATEWAYS_DIR` replaces
  `UMAI_DE_GATEWAY_ROUTES_DIR`.
- 0.8 (2026-10-20): the gateway's own input limit `UMAI_DE_GATEWAY_MAX_INPUT_CHARS` (default
  256 000 characters) instead of the engine API's 32 000; messages scanned one by one and
  cached (ADR-0037). No shape changes.
- 0.7 (2026-10-20): route management in the policy service: `GET` / `PUT /v1/gateway/routes`,
  actions `gateway.route.read` / `gateway.route.write`.
- 0.6 (2026-10-19): `fallback` routes (one retry on another upstream on 429 / 5xx / unreachable /
  timeout), response header `X-Umai-Fallback`; route-file errors name the problem.
- 0.5 (2026-10-19): `POST /v1/embeddings` with input checks and masking.
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
