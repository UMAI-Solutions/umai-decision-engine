# Errors

Every error carries a stable **code**. Program against the code, not the message: messages
may be reworded. No error message contains your content.

## Error shapes

The evaluation API answers errors as:

```json
{
  "detail": {
    "error": "invalid_credential",
    "detail": "missing or invalid API credential"
  }
}
```

Some codes add fields: `input_too_large` (`limit`, `size`, `unit`), `quota_exceeded` (`limit`,
`used`, `environment`, `resets_at`) and `invalid_request` (`problems`: where the request is
malformed and how, never what it said):

```json
{
  "detail": {
    "error": "invalid_request",
    "detail": "the request does not match the evaluation API contract",
    "problems": [
      { "loc": ["body", "contract_version"], "type": "literal_error" },
      { "loc": ["body", "<key>"], "type": "extra_forbidden" }
    ]
  }
}
```

A field name that is not part of the API is shown as `<key>`.

The AI gateway answers in OpenAI's error shape, so the OpenAI SDKs raise their usual
exceptions. `type` is `umai_gateway` for the errors below and `umai_guardrail` for refusals by
your guardrail ([AI gateway](gateway.md#refusals)); `param` names the field when there is one:

```json
{
  "error": {
    "message": "this key may not be used from this address",
    "type": "umai_gateway",
    "code": "ip_not_allowed"
  }
}
```

An error from your model provider is passed on unchanged, with the response header
`X-Umai-Upstream-Error: true`.

## Evaluation API

| HTTP | Code | Meaning | Fix |
|---|---|---|---|
| 400 | `tenant_required` | Self-hosted only: the operator token was used without `tenant_id` | Send `tenant_id`, or use an API key |
| 401 | `invalid_credential` | The key is missing, unknown, expired or revoked | Send `Authorization: Bearer <key>` with an active key; do not retry with the same key |
| 403 | `ip_not_allowed` | The key is limited to networks your request did not come from | Call from an allowed network, or add your egress address to the key ([details](authentication.md#allowed-networks)) |
| 403 | `insufficient_scope` | The key lacks the `evaluate` scope | Use a key of an application, which has it |
| 403 | `tenant_mismatch` | `tenant_id` names another organization than the key's | Leave `tenant_id` out |
| 403 | `guardrail_mismatch` | The key belongs to an application, and the request names another guardrail or a version | Leave `bundle` out |
| 403 | `unknown_application` | The key's application is disabled or was deleted | Enable the application, or use a key of an active one |
| 413 | `input_too_large` | The content is longer than your limit; nothing was evaluated | Send less: only what needs checking, long documents in parts ([Limits](limits.md#content-size)) |
| 422 | `invalid_request` | The body does not match the API: a missing or unknown field, a wrong type or value | Read `problems`: `loc` is the field, `type` the problem. `contract_version` must be `"0.12"` or an earlier accepted version |
| 422 | `bundle_required` | The key belongs to no application, so the request must name a guardrail | Send `bundle.id`, or use a key of an application |
| 429 | `rate_limited` | Too many requests for this key | Wait `Retry-After` seconds; spread your load |
| 429 | `too_many_failed_attempts` | Too many invalid keys from your address; every key from it is refused for now | Fix the key, stop retrying 401s, wait `Retry-After` |
| 429 | `quota_exceeded` | Your organization used this month's requests | Wait for `resets_at`, or contact us about your plan |
| 503 | — | The service is starting | Retry after a few seconds |

## AI gateway

| HTTP | Code | Meaning | Fix |
|---|---|---|---|
| 400 | `umai_blocked` | Your guardrail refused the call; `umai.phase` says which check | Do not retry the same content; show your user your own message |
| 400 | `umai_step_up_required` | Your guardrail asks for an approval, which an API call cannot give | Treat as a refusal, or check the content through your own approval flow |
| 400 | `invalid_request` | The body is not JSON, not an object, or lacks `model`, `messages` or `input`; or `model` has characters a model name cannot have | Fix the field named in `param` |
| 400 | `application_required` | The key belongs to no application and your organization has several | Send `X-Umai-Application`, or use a key of an application |
| 400 | `provider_key_required` | The provider is connected with your own key, and the call did not send it | Send your provider key in `X-Umai-Provider-Key` |
| 400 | `unsupported_content` | Content the guardrail cannot check: images, audio, files, token arrays, `prediction`, audio output, `prompt.variables` | Send text, or ask an administrator to let the application forward such parts unchecked |
| 401 | `invalid_credential` | The UMAI key is missing, unknown, expired or revoked | Use an active UMAI key as the SDK's API key (not your provider key) |
| 403 | `ip_not_allowed` | The key is limited to networks your request did not come from | Call from an allowed network, or add your egress address to the key |
| 403 | `insufficient_scope` | The key lacks the `gateway` scope | Use a key of an application |
| 403 | `application_mismatch` | `X-Umai-Application` names another application than the key's | Leave the header out |
| 403 | `provider_not_allowed` | The application may not use that provider, or the provider's address is not allowed on this service | Use a model of an allowed provider; ask an administrator |
| 404 | `unknown_application` | The application is disabled, deleted or unknown | Check the application in the console |
| 404 | `unknown_provider` | `@<provider>/` names no connected provider, or there is no default provider | Fix the model name, or connect the provider |
| 404 | `model_not_allowed` | The application accepts only listed models | Use a name from `GET /v1/models` |
| 413 | `request_too_large` | The body is larger than allowed | Send a shorter conversation or fewer attachments |
| 413 | `input_too_large` | The text to check is longer than allowed | Shorten the conversation |
| 429 | `rate_limited` | Too many requests for this key | Wait `Retry-After` seconds |
| 429 | `too_many_failed_attempts` | Too many invalid keys from your address | Fix the key, stop retrying 401s, wait `Retry-After` |
| 429 | `quota_exceeded` | Your organization used this month's requests | Stop retrying until next month (the SDK would retry), or contact us |
| 500 | `provider_misconfigured` | The provider's key is not set up on UMAI's side | Contact your administrator or us |
| 502 | `upstream_unreachable` | The provider could not be reached | Retry with backoff |
| 502 | `upstream_invalid_response` | The provider's answer was not valid JSON | Retry; if it persists, check the provider |
| 504 | `upstream_timeout` | The provider did not answer in time | Retry with backoff, or shorten the request |

During a stream the gateway can end with an error event instead of a status code: `code`
`upstream_timeout` (the provider stopped answering) or `upstream_invalid_response`.

## Errors inside a decision

When a part of an evaluation fails, the answer is still a decision (HTTP 200; on the gateway the
call goes on or is refused as that decision says), `degraded` is `true`, and `errors` lists what
failed. The decision already follows the guardrail's failure policy.

| Type | Meaning | Fix |
|---|---|---|
| `CONFIG_MISSING` | The guardrail (or the named version) is not available | Check the guardrail id and that a version is published and active |
| `CONFIG_INVALID` | The guardrail does not enable this phase, or it cannot be loaded | Enable the phase in the guardrail, or call with a phase it enables |
| `TIMEOUT` | Entity detection did not finish within the time budget | Retry; raise `timeout_ms`; send less content |
| `L0_ERROR` | Entity detection failed | Retry; contact us with the `event_id` if it persists |
| `TENANT_KEY_MISSING` | Your organization's key material is not available on this service | Contact us |
| `L1_NOT_IMPLEMENTED` | The guardrail has rules this version of UMAI cannot evaluate yet | Contact us |
| `L1B_TIMEOUT` | A decision-model policy did not answer in time | Retry; or raise the guardrail's decision-model timeout |
| `L1B_RATE_LIMITED` | The decision-model provider limited the calls | Retry later |
| `L1B_UNAVAILABLE` | The decision-model provider is unavailable or not configured | Retry later; contact us if it persists |
| `L1B_INVALID_RESPONSE` | The decision model answered something unusable | Contact us with the `event_id` |
| `L1B_AUTH` | The decision-model provider refused UMAI's credentials | Contact us |
