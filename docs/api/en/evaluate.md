# Evaluation API

`POST /v1/evaluate` checks one piece of content and returns the decision to act on. Call it
from your server before you send a prompt to a model, after the model answers, and around an
agent's tool calls. It works with any model and any provider.

Requires a key with the `evaluate` scope ([Authentication](authentication.md)).

## When to call

| `phase` | You check | `content.focus` |
|---|---|---|
| `PRE_LLM` | The prompt, before you send it to a model | `LAST_USER_MESSAGE` |
| `POST_LLM` | The model's answer, before you show or use it | `LAST_ASSISTANT_MESSAGE` |
| `TOOL_INPUT` | The arguments of a tool call the model wants to make; set `action.name` and `action.tool_name` | `LAST_USER_MESSAGE` |
| `TOOL_OUTPUT` | What a tool returned, before it goes back to the model | `LAST_USER_MESSAGE` |
| `MCP_REQUEST`, `MCP_RESPONSE`, `MEMORY_WRITE` | MCP calls and agent memory writes; see `action` in the [reference](reference-evaluate.md) | `LAST_USER_MESSAGE` |

Use the phases your guardrail enables (the console shows them). A phase it does not enable is
answered by the guardrail's failure policy, with `CONFIG_INVALID` in `errors`.

## Request

```json
{
  "contract_version": "0.12",
  "request_id": "req-7d2c41",
  "timestamp": "2026-10-31T09:30:00Z",
  "phase": "PRE_LLM",
  "content": {
    "messages": [
      { "role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder." }
    ],
    "focus": "LAST_USER_MESSAGE",
    "language": "tr"
  },
  "subject": { "user_id": "u-5b81", "session_id": "s-0192" },
  "destination": { "app": "support-assistant", "class": "external_llm" }
}
```

| Field | Required | What to send |
|---|---|---|
| `contract_version` | yes | `"0.12"`, the version of the request shape |
| `request_id` | yes | Your id for the call (at most 128 characters); returned in the answer |
| `timestamp` | yes | When you made the call, RFC 3339 |
| `phase` | yes | See [When to call](#when-to-call) |
| `content.messages` | yes | The conversation, oldest first: `{role, content}` with role `system`, `user` or `assistant`. The guardrail decides which part it scans; by default the last message |
| `content.focus` | yes | `LAST_USER_MESSAGE` or `LAST_ASSISTANT_MESSAGE` |
| `content.language` | no | `tr`, `en`, …; detected when absent |
| `content.artifacts` | no | Tool calls, tool results or files that belong to the check |
| `subject` | no | Who acts: `user_id`, `role`, `department`, `agent_id`, `session_id`. Use pseudonymous ids |
| `destination` | no | Where the content goes: `app` and `class` (`external_llm`, `internal_llm`, `internal_app`, `external_app`, `agent_tool`, `unknown`) |
| `action` | for agent phases | `name`, `tool_name` (`TOOL_INPUT`), `server_name` and `method` (`MCP_REQUEST`), `memory_scope` (`MEMORY_WRITE`) |
| `bundle` | no | Leave it out with an application's key. A key without an application names the guardrail: `{"id": "gr-demo-data-protection"}` |
| `timeout_ms` | no | Time budget for the evaluation, 50–60 000 ms; capped by the service |
| `flags.allow_model_calls` | no | `false` skips decision-model policies (faster); the answer is then marked `degraded` if one applied |
| `tenant_id` | no | Leave it out; the key says whose request it is |

Unknown fields are refused (**422** `invalid_request`), so a typo never goes unnoticed. The
characters of all messages and artifacts together may not exceed your limit (32 000 by default,
**413** `input_too_large`; see [Limits](limits.md)).

## Answer

```json
{
  "contract_version": "0.12",
  "request_id": "req-7d2c41",
  "tenant_id": "org-3f9a",
  "bundle": { "id": "gr-demo-data-protection", "version": 4, "mode": "ENFORCE", "sha256": "70f8e248132ea25c5a7496e6c2e5d3bbad7d354d088fa9478e6a228795b161a2" },
  "phase": "PRE_LLM",
  "decision": {
    "action": "ALLOW_WITH_MODIFICATIONS",
    "allowed": true,
    "severity": "MEDIUM",
    "reason": "L0: EMAIL_ADDRESS detected by policy İletişim bilgileri v1",
    "decided_by": "L0",
    "matched": [
      { "kind": "l0_policy", "id": "contact-data", "version": 1, "name": "İletişim bilgileri", "action": "ALLOW_WITH_MODIFICATIONS", "entities": ["EMAIL_ADDRESS"] }
    ]
  },
  "degraded": false,
  "tags": [ { "code": "data.pii.email", "layer": "L0", "score": 0.9 } ],
  "entities": [ { "type": "EMAIL_ADDRESS", "count": 1, "distinct": 1 } ],
  "output_modifications": {
    "redacted_text": "Bu e-postayı <EMAIL_ADDRESS_1> adresine gönder.",
    "entities": ["EMAIL_ADDRESS"]
  },
  "event_id": "ta-8714df5c9fb046fd8d88e5e5ee2830ff",
  "review_queued": false,
  "latency_ms": { "total": 2.3, "l0": 2.2 },
  "errors": []
}
```

Every request that passes authentication and validation gets HTTP 200 with a decision, also
when a part of the evaluation failed. The answer names entity types and policy ids, never the
values it found.

## Acting on the decision

| `decision.action` | `allowed` | What to do |
|---|---|---|
| `ALLOW` | `true` | Go on |
| `ALLOW_WITH_WARNINGS` | `true` | Go on; you may log or show the warning |
| `ALLOW_WITH_MODIFICATIONS` | `true` | Go on with `output_modifications.redacted_text` instead of the scanned message |
| `STEP_UP_APPROVAL` | `false` | Stop, unless a person in your application approves; without an approval step, treat it as `BLOCK` |
| `BLOCK` | `false` | Stop. Show your own message; `decision.reason` is for logs, not for end users |

`decision.matched` lists the policies that produced the decision, strictest first, and
`bundle` names the exact guardrail version, so every decision can be traced in the console's
records by its `event_id`.

### Masked text

With `ALLOW_WITH_MODIFICATIONS`, `redacted_text` is the text UMAI scanned (by default the last
message) with each value of the listed types replaced by a placeholder such as
`<EMAIL_ADDRESS_1>` or `<TR_TCKN_1>`. The same value gets the same placeholder within one
answer. Replace the scanned message with it before you send it on. The evaluation API does not
give the values back; if your user should see them in the model's answer, keep your own map,
or use the [AI gateway](gateway.md), which puts them back for you.

### Monitor mode

A guardrail in `MONITOR` mode records decisions without enforcing them: `decision.action` is
`ALLOW`, and `would_have` holds the decision `ENFORCE` mode would have returned. Use it to try a
guardrail on real traffic before you enforce it.

### When something fails inside UMAI

If a part of the evaluation fails (a decision model times out, the guardrail is missing), the
answer is still HTTP 200: `degraded` is `true`, `errors` says what failed, and the decision
already follows your guardrail's failure policy (for example `BLOCK` when it fails closed). Act
on `decision` as usual. The [error catalogue](errors.md#errors-inside-a-decision) lists the
types.

Decide in your own code what happens when UMAI cannot answer at all (a network error, a
timeout, HTTP 429, 5xx): fail closed (do not send the prompt) for sensitive data, or fail open
where availability matters more. Set your HTTP client's timeout a little above `timeout_ms`.

## Examples

Check a prompt, send it on (masked if needed), then check the answer.

### curl

```bash
curl https://<umai-address>/v1/evaluate \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contract_version": "0.12", "request_id": "req-7d2c41", "timestamp": "2026-10-31T09:30:00Z", "phase": "PRE_LLM", "content": {"messages": [{"role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder."}], "focus": "LAST_USER_MESSAGE"}}'
```

### Python

```python
import os
import uuid
from datetime import UTC, datetime

import httpx

UMAI = httpx.Client(
    base_url="https://<umai-address>",
    headers={"Authorization": f"Bearer {os.environ['UMAI_API_KEY']}"},
    timeout=5.0,
)


def check(phase: str, messages: list[dict], focus: str) -> dict:
    response = UMAI.post(
        "/v1/evaluate",
        json={
            "contract_version": "0.12",
            "request_id": f"req-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(UTC).isoformat(),
            "phase": phase,
            "content": {"messages": messages, "focus": focus},
        },
    )
    response.raise_for_status()  # 4xx/5xx: your own failure behaviour applies
    return response.json()


messages = [{"role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder."}]
result = check("PRE_LLM", messages, "LAST_USER_MESSAGE")
decision = result["decision"]
if not decision["allowed"]:
    raise PermissionError(f"blocked by UMAI ({result['event_id']})")
if decision["action"] == "ALLOW_WITH_MODIFICATIONS":
    messages[-1]["content"] = result["output_modifications"]["redacted_text"]

answer = call_your_model(messages)  # your model call
messages.append({"role": "assistant", "content": answer})
result = check("POST_LLM", messages, "LAST_ASSISTANT_MESSAGE")
if not result["decision"]["allowed"]:
    answer = "Sorry, I can't share that."
elif result["decision"]["action"] == "ALLOW_WITH_MODIFICATIONS":
    answer = result["output_modifications"]["redacted_text"]
```

### JavaScript

```javascript
import { randomUUID } from "node:crypto";

async function check(phase, messages, focus) {
  const response = await fetch("https://<umai-address>/v1/evaluate", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.UMAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      contract_version: "0.12",
      request_id: `req-${randomUUID()}`,
      timestamp: new Date().toISOString(),
      phase,
      content: { messages, focus },
    }),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    // 4xx/5xx: your own failure behaviour applies
    const body = await response.json().catch(() => ({}));
    throw new Error(`UMAI ${response.status} ${body.detail?.error ?? ""}`);
  }
  return response.json();
}

const messages = [{ role: "user", content: "Bu e-postayı ayse.yilmaz@example.com adresine gönder." }];
const result = await check("PRE_LLM", messages, "LAST_USER_MESSAGE");
if (!result.decision.allowed) throw new Error(`blocked by UMAI (${result.event_id})`);
if (result.decision.action === "ALLOW_WITH_MODIFICATIONS") {
  messages.at(-1).content = result.output_modifications.redacted_text;
}
```

### A tool call

Before your agent runs a tool, check the arguments the model chose:

```json
{
  "contract_version": "0.12",
  "request_id": "req-7d2c42",
  "timestamp": "2026-10-31T09:30:02Z",
  "phase": "TOOL_INPUT",
  "content": {
    "messages": [
      { "role": "assistant", "content": "{\"customer_id\": \"10000000146\", \"fields\": [\"credit_limit\"]}" }
    ],
    "focus": "LAST_USER_MESSAGE",
    "content_type": "json"
  },
  "action": { "name": "lookup_customer", "tool_name": "lookup_customer" }
}
```

The guardrail must enable `TOOL_INPUT`; policies and exceptions can then refer to the tool's
name.
