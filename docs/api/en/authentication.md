# Authentication

Every call to `/v1` carries a UMAI API key:

```text
Authorization: Bearer umai_live_eu_Q7h2kP9c_<secret>
```

Send the key in this header only, never in a URL. A missing, unknown, expired or revoked key is
answered with **401** `invalid_credential`.

## API keys

Create keys in the console under **Applications**, on the application the key is for. The
console shows the key once, when you create it; UMAI keeps only a hash of the secret, so a lost
key cannot be shown again: create a new one.

```text
umai_<environment>_<region>_<key id>_<secret>
```

| Part | Values | Meaning |
|---|---|---|
| environment | `live`, `test` | Test keys have their own, smaller monthly quota; use them while you integrate |
| region | `eu`, `us`, `sh` | Where the key was created; `sh` is a self-hosted installation |
| key id | 8 letters and digits | Not secret: it appears in the console and in records |
| secret | 43 letters and digits | The secret part |

Keep keys on your servers, in your secret store or environment variables. A key in a browser,
a mobile app or a repository can be copied by anyone who sees it.

## What a key may call

A key carries **scopes**:

| Scope | Allows |
|---|---|
| `evaluate` | `POST /v1/evaluate` |
| `gateway` | The AI gateway: `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models` |

A key created for an application has both. A key without the scope a route needs is answered
with **403** `insufficient_scope`.

## Keys and applications

A key belongs to one **application**, and the application decides which guardrail applies:

- On the evaluation API, leave `bundle` out of the request; the application's guardrail is
  used. Naming another guardrail is refused with **403** `guardrail_mismatch`.
- On the AI gateway, the key selects the application; an `X-Umai-Application` header naming
  another application is refused with **403** `application_mismatch`.
- If the application was disabled or deleted, its keys are refused with `unknown_application`
  (**403** on the evaluation API, **404** on the gateway).

A key that belongs to no application must name the
guardrail in `bundle.id` on the evaluation API (**422** `bundle_required` otherwise) and, when
the organization has several applications, the application in `X-Umai-Application` on the
gateway (**400** `application_required` otherwise).

## Allowed networks

An owner or administrator can limit a key to the networks your servers call from (IPv4 and IPv6
ranges, up to 50, in the console's key settings). The same key used from any other address is
answered with **403** `ip_not_allowed`, before anything is checked:

```json
{
  "detail": {
    "error": "ip_not_allowed",
    "detail": "this key may not be used from this address"
  }
}
```

UMAI sees the address your traffic leaves from: behind NAT, a proxy or a cloud egress gateway
that is the public address of that device, not your server's private one. If you get
`ip_not_allowed` unexpectedly, check your egress address and add its range to the key.

## Too many invalid keys

An address that presents too many invalid keys (20 within 60 seconds) is refused for a while:
every request from it gets **429** `too_many_failed_attempts` with a `Retry-After` header, even
with a valid key, and the key is not checked. An IPv6 address counts with its whole /64 network.

This protects your keys against guessing. It also means that a deployment retrying with a
wrong or revoked key can block its neighbours behind the same address. Do not retry a 401:
fix the key, then wait for `Retry-After`.

## Rotating and revoking keys

1. Create a new key for the application.
2. Deploy it to your servers.
3. Revoke the old key in the console.

A revoked key stops working within a short time; do not rely on it after revoking. If a key may
have leaked, revoke it first and create a new one. Revoking a key does not affect the
application's other keys.
