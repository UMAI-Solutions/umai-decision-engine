# Using the UMAI gateway

A quick start for developers and pilot customers. The full rules are in the contract
[`gateway-api`](../contracts/gateway-api.md); the design and its reasons in
[`architecture/ai-gateway.md`](../architecture/ai-gateway.md) and ADR-0034 to ADR-0036.

## 1. Run it locally

```powershell
python dev/local_stack.py gateway        # port 9300; shares .data/gateway-routes with the policy service
```

Without the local stack: `uvicorn umai_decision_engine.gateway.app:app --port 9300` reads
`gateway-routes/<tenant>.yaml` and `policies/` from the working directory.

## 2. Add a route

A route maps a model name your applications send to an upstream model and a guardrail.
`.data/gateway-routes/console.yaml` (tenant `console` in the local stack):

```yaml
routes:
  - route_id: gpt-eu
    upstream: { kind: openai, base_url: https://api.openai.com/v1, model: gpt-4.1-mini }
    credential: pass_through            # the app sends its provider key per call
    guardrail: gr-demo-data-protection
  - route_id: gpt-azure
    upstream:
      kind: azure_openai
      base_url: https://<resource>.openai.azure.com/openai/v1/
      model: <deployment>
    credential: secret                  # a key the deployment holds
    secret: azure
    guardrail: gr-demo-data-protection
```

For `credential: secret`, give the gateway the key in its environment:
`UMAI_DE_GATEWAY_PROVIDER_KEYS={"azure": "<key>"}`. Routes reload within 5 seconds; a broken file
keeps the last good routes and logs `routes.invalid` with the problem. Through the policy service:
`PUT /v1/gateway/routes` with `{"routes": [...]}`.

## 3. Call it with the OpenAI SDK

Change two things in the application: the base URL and the key (a UMAI API key with the `gateway`
scope, or the operator token in the local stack).

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:9300/v1",
    api_key="<UMAI key>",
    default_headers={"X-Umai-Provider-Key": "<provider key>"},  # pass_through routes only
)

answer = client.chat.completions.create(
    model="gpt-eu",
    messages=[{"role": "user", "content": "TC kimlik 10000000146 olan müşteri için not yaz"}],
)
```

What happens: the national id reaches the model as `<TR_TCKN_1>`; the answer comes back with
the caller's own value restored. Streaming (`stream=True`), the Responses API
(`client.responses.create`), embeddings (`client.embeddings.create`) and tool calls work the same
way. Optional headers describe the caller for the guardrail's exceptions: `X-Umai-Session-Id`,
`X-Umai-Subject-User`, `X-Umai-Subject-Role`, `X-Umai-Subject-Department`, `X-Umai-Agent-Id`.

## 4. When the guardrail refuses

- `refusal: error` (default): HTTP 400 with `error.code` `umai_blocked`; the SDK raises
  `openai.BadRequestError`. `error.umai` names the phase, the entity types and the policies, never
  the values.
- `refusal: message`: HTTP 200 with the route's `refusal_text` and `finish_reason:
  "content_filter"` (Responses: `status: "incomplete"`), for chat screens that cannot show errors.

Response headers tell what happened: `X-Umai-Input-Decision`, `X-Umai-Output-Decision`,
`X-Umai-Event-Id`, `X-Umai-Fallback`, `X-Umai-Unscanned-Parts`.

## 5. Common errors

| HTTP | `code` | Fix |
|---|---|---|
| 401 | `invalid_credential` | Send the UMAI key in `Authorization: Bearer` |
| 403 | `insufficient_scope` | Create the key with the `gateway` scope |
| 404 | `unknown_route` | `model` must be a `route_id` of your tenant (`GET /v1/models`) |
| 400 | `provider_key_required` | Add `X-Umai-Provider-Key`, or switch the route to `credential: secret` |
| 400 | `unsupported_content` | Images, audio or files on a route with `unscanned_parts: reject` |
| 500 | `route_misconfigured` | The route's `secret` is missing from `UMAI_DE_GATEWAY_PROVIDER_KEYS` |
| 502 / 504 | `upstream_unreachable` / `upstream_timeout` | Upstream down or slow; add a `fallback` route |

## 6. Measure

```powershell
python -m evals.run --suite gateway-latency --tag <label>           # what the gateway adds per call
python -m evals.run --suite masking-survival --model gpt-4.1-mini   # live; UMAI_EVAL_OPENAI_API_KEY
```
