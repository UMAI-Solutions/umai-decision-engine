# Using the UMAI gateway

A quick start for developers and pilot customers. The full rules are in the contract
[`gateway-api`](../contracts/gateway-api.md). The design and its reasons are in
[`architecture/ai-gateway.md`](../architecture/ai-gateway.md), ADR-0034 to ADR-0036 and ADR-0038.

## 1. Run it locally

```powershell
python dev/local_stack.py gateway        # port 9300; shares .data/gateways with the policy service
```

Without the local stack, `uvicorn umai_decision_engine.gateway.app:app --port 9300` reads
`gateways/<tenant>.yaml` and `policies/` from the working directory.

## 2. Create a gateway

A gateway is one provider connection plus one guardrail. Whatever model name your application
sends goes to that provider unchanged, so a new model works without any configuration.

In the local stack the file is `.data/gateways/console.yaml` (tenant `console`):

```yaml
gateways:
  - gateway_id: hr-assistant
    provider: { kind: openai }            # base_url defaults to https://api.openai.com/v1
    guardrail: gr-demo-data-protection    # the app sends its OpenAI key per call (pass_through)
  - gateway_id: support-azure
    provider:
      kind: azure_openai
      base_url: https://<resource>.openai.azure.com/openai/v1/
      credential: secret                  # a key the deployment holds
      secret: azure
    guardrail: gr-demo-data-protection
    models: [gpt-4.1-mini]                # optional: only these names (and aliases)
    aliases:                              # optional: name the app sends -> Azure deployment
      - { name: gpt-4.1-mini, model: <deployment> }
```

- **Deployment-held keys.** For `credential: secret`, give the gateway the key in its environment:
  `UMAI_DE_GATEWAY_PROVIDER_KEYS={"azure": "<key>"}`.
- **Reloading.** Gateways reload within 5 seconds. A broken file keeps the last good gateways and
  logs `gateways.invalid` with the problem.
- **Policy service.** `PUT /v1/gateways/<id>` with one gateway, `GET /v1/gateways`, and
  `DELETE /v1/gateways/<id>`.
- **Keys.** A key belongs to one gateway: `POST /v1/tenants/<tenant>/keys` with
  `{"name": "hr-bot", "gateway_id": "hr-assistant"}`. Its scope is then `gateway`.

## 3. Call it with the OpenAI SDK

Change two things in the application: the base URL and the key. Use a UMAI key bound to the
gateway, or the operator token in the local stack.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:9300/v1",
    api_key="<UMAI key>",
    default_headers={"X-Umai-Provider-Key": "<provider key>"},  # pass_through gateways only
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

**Several gateways.** A key that is not bound to a gateway (the operator token, older keys) names
the gateway with `X-Umai-Gateway: <id>`. It can leave the header out when the tenant has only one
gateway. Every answer says which gateway handled it in `X-Umai-Gateway`.

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
- **`refusal: message`.** HTTP 200 with the gateway's `refusal_text` and `finish_reason:
  "content_filter"` (Responses: `status: "incomplete"`). This is for chat screens that cannot show
  errors.

Response headers tell what happened:
- `X-Umai-Gateway`;
- `X-Umai-Input-Decision` and `X-Umai-Output-Decision`;
- `X-Umai-Event-Id`;
- `X-Umai-Fallback`;
- `X-Umai-Unscanned-Parts`.

## 5. Common errors

| HTTP | `code` | Fix |
|---|---|---|
| 401 | `invalid_credential` | Send the UMAI key in `Authorization: Bearer` |
| 403 | `insufficient_scope` | Create the key with the `gateway` scope (or bound to a gateway) |
| 400 | `gateway_required` | The tenant has several gateways: send `X-Umai-Gateway`, or use a key bound to one |
| 403 | `gateway_mismatch` | `X-Umai-Gateway` names another gateway than the key's: drop the header |
| 404 | `unknown_gateway` | No such enabled gateway (or the key's gateway was disabled) |
| 404 | `model_not_allowed` | The gateway has a `models` list; add the name or an alias |
| 400 | `provider_key_required` | Add `X-Umai-Provider-Key`, or switch the gateway to `credential: secret` |
| 400 | `unsupported_content` | Images, audio or files on a gateway with `unscanned_parts: reject` |
| 500 | `gateway_misconfigured` | The gateway's `secret` is missing from `UMAI_DE_GATEWAY_PROVIDER_KEYS` |
| 502 / 504 | `upstream_unreachable` / `upstream_timeout` | Provider down or slow; add a `fallback` gateway |

## 6. Measure

```powershell
python -m evals.run --suite gateway-latency --tag <label>           # what the gateway adds per call
python -m evals.run --suite masking-survival --model gpt-4.1-mini   # live; UMAI_EVAL_OPENAI_API_KEY
```
