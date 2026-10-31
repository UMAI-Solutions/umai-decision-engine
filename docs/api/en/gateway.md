# AI gateway

The AI gateway speaks OpenAI's API. Point an OpenAI SDK at UMAI with a UMAI key, and every call
is checked by your application's guardrail on the way in and on the way out: prompts, answers,
tool calls and tool results. Values the guardrail masks never reach the model; masked values
from your own messages are put back into the answer before you receive it.

Requires a key with the `gateway` scope ([Authentication](authentication.md)).

| Endpoint | OpenAI API | Checks |
|---|---|---|
| `POST /v1/chat/completions` | Chat Completions, streaming included | Input, tool results, answer, tool calls |
| `POST /v1/responses` | Responses API, streaming included | Input, tool outputs, answer, tool calls |
| `POST /v1/embeddings` | Embeddings | Input (each string) |
| `GET /v1/models` | Models | None; never counts toward your quota |

## Connecting

| Setting | Value |
|---|---|
| Base URL | `https://<umai-address>/v1` |
| API key | Your UMAI key: `Authorization: Bearer <UMAI key>` |
| `X-Umai-Provider-Key` | Your provider key (e.g. OpenAI's), when the provider is connected with your own key. That is the case for the built-in OpenAI provider. UMAI sends it to the provider only; it is never stored or logged |

Your organization connects providers (OpenAI, Azure OpenAI, OpenAI-compatible servers) in the
console. A provider set up with a key UMAI holds needs no `X-Umai-Provider-Key`.

### Which application and which model

- The key's application is used. A key without an application uses the `X-Umai-Application`
  header, or the organization's only application.
- `model` is passed to the provider as you send it, unless the application maps it: an
  application can limit the names it accepts (**404** `model_not_allowed`) and define aliases.
- `@<provider>/<model>` sends the call to another provider your organization connected, e.g.
  `@azure-eu/gpt-4o-deployment`.
- `GET /v1/models` lists the names the application offers.

## What is checked

1. **Input.** Every message the gateway forwards, the history included (a value masked in an
   earlier turn is in the history again and is masked again), and the arguments of earlier tool
   calls. With `TOOL_OUTPUT` enabled in the guardrail, each tool result is checked on its own with
   the tool's name.
2. **Answer.** Each choice's text, after the model answers.
3. **Tool calls.** Each tool call in the answer, with the tool's name, when the guardrail enables
   `TOOL_INPUT`. Masked arguments stay valid JSON.

The call goes to the provider only if every input check allows it. Text parts are checked;
images, audio and files cannot be checked and are refused with **400** `unsupported_content`
unless your application is set to forward them unchecked (the `X-Umai-Unscanned-Parts` response
header then counts them).

Some request fields would let content bypass the checks, so the gateway handles them:
`prediction`, audio output and Responses `prompt.variables` are refused (**400**
`unsupported_content`); `metadata`, the `name` of messages, `logprobs` / `top_logprobs` and
reasoning summaries are not forwarded. Every other field is forwarded unchanged.

### Masking and restoring

With `ALLOW_WITH_MODIFICATIONS` the gateway replaces each value with a placeholder such as
`<TR_TCKN_1>` before the call. If the model's answer contains a placeholder that came from your
messages, the gateway puts the value back, so your user sees what they wrote. Values the model
produced itself stay masked. An application that must never show values back can be set not
to restore them.

## Refusals

By default a refusal is **HTTP 400** in OpenAI's error shape; the OpenAI SDKs raise
`BadRequestError`. `type` is `umai_guardrail`, `code` is `umai_blocked`
(`umai_step_up_required` for an approval decision, which an API cannot ask for), and `umai`
says why with types and ids, never the values:

```json
{
  "error": {
    "message": "Blocked by UMAI guardrail gr-demo-data-protection: L0: TR_TCKN detected by policy Kimlik verileri v2",
    "type": "umai_guardrail",
    "code": "umai_blocked",
    "umai": {
      "request_id": "gw-3f9c1e0b",
      "event_id": "ta-195c97496f4748b88442e60f6ed6e1b9",
      "phase": "PRE_LLM",
      "action": "BLOCK",
      "entities": ["TR_TCKN"],
      "matched": ["identity-data"]
    }
  }
}
```

`umai.phase` says which check refused: `PRE_LLM` (your input, nothing reached the provider),
`TOOL_OUTPUT` (a tool result), `POST_LLM` (the answer) or `TOOL_INPUT` (a tool call in the
answer).

An application can instead answer refusals as a normal completion, for chat interfaces that
cannot show errors: HTTP 200, the configured text as the message, and
`finish_reason: "content_filter"` (Responses API: `status: "incomplete"` with
`incomplete_details.reason: "content_filter"`).

## Streaming

`"stream": true` works as with OpenAI. Your application's stream mode decides how answers are
checked while they flow:

| Mode | What you see |
|---|---|
| `window` (default) | The text arrives slightly behind the model (a short window is held back and checked). A block ends the choice with `finish_reason: "content_filter"` |
| `buffer` | The whole answer is checked before the first event; a block can still be an HTTP 400 |
| `after` | The stream is not held back; the answer is checked at the end and the decision recorded, not enforced |

Tool calls in a stream arrive complete, after they are checked. If the provider stops answering
mid-stream, an error event with `code: upstream_timeout` comes before `data: [DONE]`.

## Response headers

| Header | Meaning |
|---|---|
| `X-Umai-Request-Id` | The call's id (yours from the request header of the same name, or generated); quote it when you contact us |
| `X-Umai-Application` | The application the call ran as |
| `X-Umai-Event-Id` | The recorded decision of the input check |
| `X-Umai-Input-Decision` | The input check's action |
| `X-Umai-Output-Decision` | The strictest action of the answer checks (non-streaming and `buffer`) |
| `X-Umai-Unscanned-Parts` | Parts forwarded unchecked |
| `X-Umai-Upstream-Error` | `true`: the body is the provider's own error, passed on unchanged |
| `X-Umai-Fallback` | The application's fallback provider answered |

Optional request headers describe who acts, for policies that use it and for your records:
`X-Umai-Session-Id`, `X-Umai-Subject-User` (pseudonymous; else the body's `safety_identifier`
or `user`), `X-Umai-Subject-Role`, `X-Umai-Subject-Department`, `X-Umai-Agent-Id`.

## Examples

### curl

```bash
curl https://<umai-address>/v1/chat/completions \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "X-Umai-Provider-Key: $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Bu e-postayı ayse.yilmaz@example.com adresine gönder."}]}'
```

### Python

```python
import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url="https://<umai-address>/v1",
    api_key=os.environ["UMAI_API_KEY"],
    default_headers={"X-Umai-Provider-Key": os.environ["OPENAI_API_KEY"]},
)

try:
    answer = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "user", "content": "10000000146 numaralı müşterinin kredi limitini özetle."}
        ],
        extra_headers={"X-Umai-Session-Id": "s-0192"},
    )
    print(answer.choices[0].message.content)
except openai.BadRequestError as error:
    if error.code in ("umai_blocked", "umai_step_up_required"):
        umai = error.body["umai"]  # why, with types and ids only
        print(f"refused at {umai['phase']}: {umai['entities']} (event {umai['event_id']})")
    else:
        raise

# Streaming
stream = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[
        {"role": "user", "content": "Müşteri hizmetleri için kısa bir karşılama mesajı yaz."}
    ],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
    if chunk.choices and chunk.choices[0].finish_reason == "content_filter":
        print("\n[stopped by your organization's AI policy]")

# Responses API and embeddings use the same client
response = client.responses.create(model="gpt-4.1-mini", input="Kısa bir teşekkür notu yaz.")
vectors = client.embeddings.create(
    model="text-embedding-3-small", input=["İade talebi için başvuru numaranızı kullanın."]
)
```

### JavaScript

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://<umai-address>/v1",
  apiKey: process.env.UMAI_API_KEY,
  defaultHeaders: { "X-Umai-Provider-Key": process.env.OPENAI_API_KEY },
});

try {
  const answer = await client.chat.completions.create({
    model: "gpt-4.1-mini",
    messages: [{ role: "user", content: "10000000146 numaralı müşterinin kredi limitini özetle." }],
  });
  console.log(answer.choices[0].message.content);
} catch (error) {
  if (error instanceof OpenAI.APIError && error.code === "umai_blocked") {
    const umai = error.error.umai; // why, with types and ids only
    console.log(`refused at ${umai.phase}: ${umai.entities} (event ${umai.event_id})`);
  } else {
    throw error;
  }
}
```

### Retries in the OpenAI SDKs

The OpenAI SDKs retry 429 and 5xx answers twice by default. A guardrail refusal (400) is never
retried. `quota_exceeded` does not clear before the next month: check `error.code` and stop
retrying (see [Limits](limits.md)).
