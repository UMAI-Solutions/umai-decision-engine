# Using the UMAI gateway

A quick start for developers and pilot customers. The full rules are in the contract
[`gateway-api`](../contracts/gateway-api.md). The design and its reasons are in
[`architecture/ai-gateway.md`](../architecture/ai-gateway.md), ADR-0034 to ADR-0036, ADR-0038 and
ADR-0039. In the console: **Applications** (add one, copy its snippet) and **AI Gateway**
(providers, settings).

## 1. Run it locally

```powershell
python dev/local_stack.py gateway        # port 9300; shares .data/applications with the policy service
```

Without the local stack, `uvicorn umai_decision_engine.gateway.app:app --port 9300` reads
`applications/<tenant>.yaml` and `policies/` from the working directory.

## 2. Add an application

An **application** is what UMAI protects: one guardrail on every path (gateway and evaluation
API) and its own keys. The AI gateway sends its model calls to a **provider**. A built-in OpenAI
provider exists in every organization and is the default, so an application that sends its own
OpenAI key needs no provider setup. Model names pass through unchanged; `@<provider>/<model>`
picks another provider.

In the local stack the file is `.data/applications/console.yaml` (tenant `console`):

```yaml
providers:                                # optional: the built-in `openai` is always there
  - provider_id: support-azure            # model prefix: @support-azure/<deployment>
    kind: azure_openai
    base_url: https://<resource>.openai.azure.com/openai/v1/
    credential: secret                    # a key the deployment holds
    secret: azure
applications:
  - application_id: hr-assistant          # bare names go to the default provider (OpenAI)
    guardrail: gr-demo-data-protection    # the app sends its OpenAI key per call (pass_through)
  - application_id: support-bot
    guardrail: gr-demo-data-protection
    providers: [support-azure]            # optional: only these providers
    aliases:                              # optional: name the app sends -> target
      - { name: gpt-4.1-mini, model: "@support-azure/<deployment>" }
```

- **Deployment-held keys.** For `credential: secret`, give the gateway the key in its environment:
  `UMAI_DE_GATEWAY_PROVIDER_KEYS={"azure": "<key>"}`.
- **Reloading.** The file reloads within 5 seconds. A broken file keeps the last good content and
  logs `applications.invalid` with the problem.
- **Policy service.** `GET /v1/applications`, `PUT` / `DELETE /v1/applications/<id>`;
  `GET /v1/providers`, `PUT` / `DELETE /v1/providers/<id>`; `GET` / `PUT /v1/gateway/settings`
  for the defaults (refusal, streaming, unscanned content, restoring, timeouts).
- **Keys.** A key belongs to one application: `POST /v1/tenants/<tenant>/keys` with
  `{"name": "hr-bot", "application_id": "hr-assistant"}`. Its scopes are then `evaluate` and
  `gateway`: the same key works on the gateway and on `/v1/evaluate`, where the application's
  guardrail applies.

## 3. Call it with the OpenAI SDK

Change two things in the application: the base URL and the key. Use the application's UMAI key,
or the operator token in the local stack.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:9300/v1",
    api_key="<UMAI key>",
    default_headers={"X-Umai-Provider-Key": "<provider key>"},  # pass_through providers only
)

answer = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "TC kimlik 10000000146 olan müşteri için not yaz"}],
)
```

**What happens.** The national id reaches the model as `<TR_TCKN_1>`. The answer comes back with
the caller's own value restored.

**Other surfaces.** These work the same way:
- streaming (`stream=True`);
- the Responses API (`client.responses.create`);
- embeddings (`client.embeddings.create`);
- tool calls.

**Several applications.** A key that belongs to no application (the operator token, older keys)
names one with `X-Umai-Application: <id>`. It can leave the header out when the tenant has only
one application. Every answer names the application in `X-Umai-Application`.

**Caller details.** Optional headers describe the caller for the guardrail's exceptions:
- `X-Umai-Session-Id`;
- `X-Umai-Subject-User`;
- `X-Umai-Subject-Role`;
- `X-Umai-Subject-Department`;
- `X-Umai-Agent-Id`.

## 4. When the guardrail refuses

- **`refusal: error` (default).** HTTP 400 with `error.code` `umai_blocked`; the SDK raises
  `openai.BadRequestError`. `error.umai` names the phase, the entity types and the policies, never
  the values.
- **`refusal: message`.** HTTP 200 with the `refusal_text` and `finish_reason:
  "content_filter"` (Responses: `status: "incomplete"`). This is for chat screens that cannot show
  errors.

Response headers tell what happened:
- `X-Umai-Application`;
- `X-Umai-Input-Decision` and `X-Umai-Output-Decision`;
- `X-Umai-Event-Id`;
- `X-Umai-Fallback`;
- `X-Umai-Unscanned-Parts`.

## 5. Common errors

| HTTP | `code` | Fix |
|---|---|---|
| 401 | `invalid_credential` | Send the UMAI key in `Authorization: Bearer` |
| 403 | `insufficient_scope` | Create the key with the `gateway` scope (or for an application) |
| 400 | `application_required` | The tenant has several applications: send `X-Umai-Application`, or use the application's key |
| 403 | `application_mismatch` | `X-Umai-Application` names another application than the key's: drop the header |
| 404 | `unknown_application` | No such enabled application (or the key's application was disabled) |
| 404 | `unknown_provider` | `@<provider>/` names no enabled provider, or there is no default provider |
| 403 | `provider_not_allowed` | The application's `providers` list leaves this provider out |
| 404 | `model_not_allowed` | The application has a `models` list; add the name or an alias |
| 400 | `provider_key_required` | Add `X-Umai-Provider-Key`, or switch the provider to `credential: secret` |
| 400 | `unsupported_content` | Images, audio or files with `unscanned_parts: reject` |
| 500 | `provider_misconfigured` | The provider's `secret` is missing from `UMAI_DE_GATEWAY_PROVIDER_KEYS` |
| 502 / 504 | `upstream_unreachable` / `upstream_timeout` | Provider down or slow; give the application a `fallback` target |

The gateway logs these refusals as `gateway.refused` with the request id and the code.

## 6. Measure

```powershell
python -m evals.run --suite gateway-latency --tag <label>           # what the gateway adds per call
python -m evals.run --suite masking-survival --model gpt-4.1-mini   # live; UMAI_EVAL_OPENAI_API_KEY
```
