# UMAI API documentation

UMAI checks what your applications send to AI models and what comes back: personal data
(Turkish identity numbers, IBANs, card numbers, e-mail addresses and more), secrets, prompt
attacks and misuse, against the **guardrail** your organization set up in the UMAI console. A
check ends in a decision: let the content through, let it through with the sensitive values
masked, or stop it.

There are two ways to connect an application.

| | AI gateway | Evaluation API |
|---|---|---|
| What you change | The base URL and the API key of your OpenAI SDK | You call UMAI before and after your model call |
| Checks | Prompts, answers, tool calls and tool results, automatically | The content you send, at the step you choose |
| Masked values | Put back into the answer for you | You receive the masked text |
| Models | OpenAI, Azure OpenAI and OpenAI-compatible providers your organization connected | Any model or provider, or none at all |
| Endpoints | `POST /v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `GET /v1/models` | `POST /v1/evaluate` |
| Guide | [AI gateway](gateway.md) | [Evaluation API](evaluate.md) |

## Before you start

1. In the console, open **Applications**, create an application and choose its guardrail
   (your organization starts with a starter guardrail you can use right away).
2. Create a key for the application. The key is shown once; keep it in your secret store.
   See [Authentication](authentication.md).
3. Find your **UMAI address** on the console's **API & SDKs** page. The examples below write it
   as `https://<umai-address>`.

Call UMAI from your servers only. A key in a browser or a mobile app can be read by anyone who
uses it.

## First call through the AI gateway

The gateway speaks OpenAI's API. Point the official SDK at UMAI and send your OpenAI key in
`X-Umai-Provider-Key` (when your organization uses the built-in OpenAI provider):

```bash
curl https://<umai-address>/v1/chat/completions \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "X-Umai-Provider-Key: $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Write a short welcome message for new customers."}]}'
```

```python
import os

from openai import OpenAI

client = OpenAI(
    base_url="https://<umai-address>/v1",
    api_key=os.environ["UMAI_API_KEY"],
    default_headers={"X-Umai-Provider-Key": os.environ["OPENAI_API_KEY"]},
)
answer = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "Write a short welcome message for new customers."}],
)
print(answer.choices[0].message.content)
```

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://<umai-address>/v1",
  apiKey: process.env.UMAI_API_KEY,
  defaultHeaders: { "X-Umai-Provider-Key": process.env.OPENAI_API_KEY },
});
const answer = await client.chat.completions.create({
  model: "gpt-4.1-mini",
  messages: [{ role: "user", content: "Write a short welcome message for new customers." }],
});
console.log(answer.choices[0].message.content);
```

## First call to the evaluation API

```bash
curl https://<umai-address>/v1/evaluate \
  -H "Authorization: Bearer $UMAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contract_version": "0.12", "request_id": "req-0001", "timestamp": "2026-10-31T09:30:00Z", "phase": "PRE_LLM", "content": {"messages": [{"role": "user", "content": "10000000146 numaralı müşterinin kredi limitini özetle."}], "focus": "LAST_USER_MESSAGE"}}'
```

The test identity number `10000000146` is blocked by the starter guardrail; the answer says so
in `decision.action` and never repeats the number. The [Evaluation API](evaluate.md) guide shows
the same call in Python and JavaScript.

## Versions

Each API has a contract version, shown at the bottom of every page. A minor version adds
something (a field, an error code) without breaking existing callers; we tell design partners
before a change that needs work on their side. On the evaluation API you also send the version
of the request shape you use, `contract_version` (currently `"0.12"`): it changes only when the
request changes, so it can be behind the API's version.

The machine-readable descriptions are in the reference pages: the
[evaluation API](reference-evaluate.md) and the [AI gateway](reference-gateway.md).

## Your data

- UMAI's answers, error messages and records name entity types (`TR_TCKN`, `EMAIL_ADDRESS`)
  and ids, never the values it found.
- Use pseudonymous ids in `subject.user_id`, `X-Umai-Subject-User` and `request_id`, not e-mail
  addresses or names.
- Use synthetic test data while you integrate, like the values on these pages.

## Getting help

Quote the `event_id` of an evaluation, or the `X-Umai-Request-Id` response header of a gateway
call, when you contact us: we can find the decision without seeing your content.
