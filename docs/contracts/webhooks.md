# Contract: webhooks (outbound events to an organization's endpoints, Slack)

**Version:** 0.1 · **Producer → consumer:** console ↔ policy service (`/v1/webhooks…`), policy
service → the organization's receivers (generic HTTPS endpoints, Slack incoming webhooks) ·
**Decision:** [ADR-0048](../adr/0048-outbound-webhooks-and-slack.md) · **Facts:**
[Slack incoming webhooks](../reference/slack-incoming-webhooks.md)

An organization registers endpoints; the policy service sends them events about the organization:
blocked decisions (aggregated), monthly quota thresholds and billing changes. Payloads carry ids,
types, codes, counts and links to the console, never content (ADR-0005): not even redacted text,
entity values, spans or hashes. Delivery is at least once, unordered; receivers deduplicate on
`event_id`.

## Roles

`webhook.read`: owner, admin (and the operator). `webhook.manage` (create, change, delete, rotate
the secret, send a test): owner, admin. A person is always named (`created_by`).

## Event types

| Type | When | `data` |
|---|---|---|
| `decision.blocked` | Blocked decisions (`action` `BLOCK`) of one aggregation window (default 5 minutes, read one minute after the window ends): one event per window with at least one block, never one per call | `window`, `blocked`, `by_source`, `by_phase`, top 5 `applications`, `top_entities` (types), `top_policies` (kind and id), up to 5 `sample_event_ids`, `scanned`, `truncated` |
| `quota.threshold` | Requests this month (UTC) reach 80 % or 100 % of the plan's monthly limit (entitlements); once per month, environment, threshold and limit | `environment` (`live`, `test`), `threshold_percent`, `limit`, `used`, `month`, `resets_at`, `edition` |
| `billing.payment_succeeded` | A subscription payment is in (billing 0.1) | `amount`, `currency`, `edition`, `subscription_status` |
| `billing.payment_failed` | A subscription payment failed; the subscription is past due | as above, and `grace_until` |
| `billing.subscription_changed` | Started, plan changed, cancelled (by the organization or at the provider), grace ended, ended | `change`, `edition`, `effective` (plan changes), `subscription_status` |
| `webhook.test` | Only from the test route; cannot be subscribed to | `endpoint_id` |

An endpoint receives the events that happen while it is enabled: an endpoint created or enabled
again gets nothing older. `quota.threshold` reports the month's state, so an endpoint added after a
threshold was crossed receives it once. After the policy service was down, events up to one hour
old are still sent.

## Generic delivery

`POST` to the endpoint's URL, body:

```json
{
  "contract_version": "0.1",
  "event_id": "evt_6f1d0c3a9b8e47d2a5c4b3e2f1a0d9c8",
  "type": "decision.blocked",
  "occurred_at": "2026-10-30T12:05:00Z",
  "organization_id": "acme-security",
  "data": {
    "window": { "from": "2026-10-30T12:00:00Z", "to": "2026-10-30T12:05:00Z" },
    "blocked": 12,
    "by_source": { "gateway": 10, "api": 2 },
    "by_phase": { "PRE_LLM": 11, "POST_LLM": 1 },
    "applications": [
      { "application_id": "support-bot", "blocked": 10 },
      { "application_id": null, "blocked": 2 }
    ],
    "top_entities": [{ "type": "TR_TCKN", "decisions": 7 }],
    "top_policies": [{ "kind": "l0_policy", "id": "tr-national-id", "decisions": 7 }],
    "sample_event_ids": ["ev_0b7c4e2f9a1d4c3b8e6f5a4d3c2b1a09"],
    "scanned": 12,
    "truncated": false
  },
  "links": { "console": "https://console.example.com/detections" }
}
```

```json
{
  "contract_version": "0.1",
  "event_id": "evt_0a9b8c7d6e5f40312a3b4c5d6e7f8091",
  "type": "quota.threshold",
  "occurred_at": "2026-10-30T12:07:31Z",
  "organization_id": "acme-security",
  "data": {
    "environment": "live",
    "threshold_percent": 80,
    "limit": 10000,
    "used": 8012,
    "month": "2026-10",
    "resets_at": "2026-11-01T00:00:00Z",
    "edition": "community"
  },
  "links": { "console": "https://console.example.com/usage" }
}
```

Headers:

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `User-Agent` | `UMAI-Webhooks/0.1` |
| `X-Umai-Event-Id` | the body's `event_id` |
| `X-Umai-Event-Type` | the body's `type` |
| `X-Umai-Delivery-Attempt` | `1` to `6` |
| `X-Umai-Signature` | `t=<unix seconds>,v1=<hex>`; during 24 hours after a secret rotation also a second `v1=` signed with the previous secret |

A `2xx` answer is a delivery; anything else (a `3xx` included: redirects are never followed) is a
failure and the event is sent again after 1 minute, 5 minutes, 30 minutes, 2 hours and 6 hours
(six attempts in all). The answer's body is never read or stored. Each attempt has 10 seconds
(3 to connect).

### Verifying the signature

`v1` is `HMAC-SHA256(secret, "<t>." + raw body)` in lower-case hex, where `secret` is the whole
`whsec_…` string (UTF-8). Check it on the raw bytes before parsing, compare in constant time,
refuse a `t` more than 5 minutes from your clock, and ignore an `event_id` you have already
processed.

```python
import hashlib
import hmac
import time


def verify(secret: str, body: bytes, header: str, tolerance_s: int = 300) -> bool:
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    timestamps = [v for k, v in parts if k == "t"]
    signatures = [v for k, v in parts if k == "v1"]
    if len(timestamps) != 1 or not signatures or not timestamps[0].isdigit():
        return False
    if abs(time.time() - int(timestamps[0])) > tolerance_s:
        return False
    expected = hmac.new(
        secret.encode(), timestamps[0].encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)
```

The same in Node.js:

```js
const crypto = require("node:crypto");

function verify(secret, rawBody, header, toleranceS = 300) {
  const parts = header.split(",").map((p) => p.split("="));
  const t = parts.filter(([k]) => k === "t").map(([, v]) => v);
  const sigs = parts.filter(([k]) => k === "v1").map(([, v]) => v);
  if (t.length !== 1 || !/^\d+$/.test(t[0]) || sigs.length === 0) return false;
  if (Math.abs(Date.now() / 1000 - Number(t[0])) > toleranceS) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${t[0]}.`).update(rawBody).digest();
  return sigs.some((s) => {
    const given = Buffer.from(s, "hex");
    return given.length === expected.length && crypto.timingSafeEqual(given, expected);
  });
}
```

## Slack delivery

An endpoint of kind `slack` takes a Slack incoming-webhook URL (`https://hooks.slack.com/services/…`)
and receives Slack's message format (`text` and `blocks`, mrkdwn links to the console) instead of
the generic body. It is not signed: the URL is the secret, so it is masked in every answer and log.
Slack answering 403, 404 or 410 (revoked, removed, archived channel) turns the endpoint off with
reason `gone`.

## `GET /v1/webhooks`

```json
{
  "contract_version": "0.1",
  "tenant_id": "acme-security",
  "available": true,
  "event_types": [
    "decision.blocked",
    "quota.threshold",
    "billing.payment_failed",
    "billing.payment_succeeded",
    "billing.subscription_changed"
  ],
  "max_endpoints": 10,
  "endpoints": [
    {
      "endpoint_id": "8a0e2f4c-1b3d-4e5f-9a7b-6c5d4e3f2a1b",
      "kind": "generic",
      "url": "https://hooks.example.com/…take",
      "event_types": ["decision.blocked", "quota.threshold"],
      "description": "SIEM intake",
      "enabled": true,
      "signed": true,
      "consecutive_failures": 0,
      "last_success_at": "2026-10-30T12:06:02Z",
      "created_at": "2026-10-30T09:00:00Z",
      "created_by": "person:auth0|ada",
      "updated_at": "2026-10-30T09:00:00Z"
    }
  ]
}
```

`available` is `false` when the deployment has no webhook key: nothing can be created and nothing
is sent. `url` is masked: scheme, host and the last four characters. `links.console` in a payload is
`null` when the deployment does not know the console's address.

## `POST /v1/webhooks/endpoints`

Body: `{url, kind, event_types, description?, enabled?}` → 201 `{endpoint, secret}`. `secret`
(`whsec_…`) is in this answer once; `null` for `slack`. Rules for `url`: `https`, no user name or
password, no fragment, port 443 or 1024–65535, at most 2048 characters; the host must not be an IP
address or name that webhooks may not reach (private, loopback, link-local, carrier-grade NAT,
multicast, reserved, cloud metadata, `localhost`, `*.internal`, `*.local`), and when it resolves,
none of its addresses may be one. `slack`: the URL must be a Slack incoming webhook. Errors: 422
`invalid_url`, 409 `too_many_endpoints`, 503 `webhooks_unavailable`.

## `GET /v1/webhooks/endpoints/{endpoint_id}`

`{endpoint}`, as in the list; 404 `not_found` for another organization's or an unknown id.

## `PATCH /v1/webhooks/endpoints/{endpoint_id}`

Body: any of `url`, `event_types`, `description`, `enabled` → `{endpoint}`. Turning an endpoint
off cancels its pending deliveries (`disabled_reason` `manual`); turning it on clears the reason
and the failure count and starts its events from now.

## `DELETE /v1/webhooks/endpoints/{endpoint_id}`

204. Pending deliveries are dropped; the delivery log stays until retention.

## `POST /v1/webhooks/endpoints/{endpoint_id}/secret`

Rotates the signing secret of a `generic` endpoint → `{endpoint, secret}`. The previous secret
still signs (a second `v1=`) for 24 hours. 409 `not_signed` for `slack`.

## `POST /v1/webhooks/endpoints/{endpoint_id}/test`

Sends one `webhook.test` event now, also to a disabled endpoint, and answers with the attempt:
`{"delivery": {…}}`. A test is never retried and does not count towards turning the endpoint off.

## `GET /v1/webhooks/deliveries`

Query: `endpoint_id`, `limit` (1–200, default 50), `cursor`. Newest first:

```json
{
  "deliveries": [
    {
      "delivery_id": "1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b",
      "endpoint_id": "8a0e2f4c-1b3d-4e5f-9a7b-6c5d4e3f2a1b",
      "event_id": "evt_6f1d0c3a9b8e47d2a5c4b3e2f1a0d9c8",
      "event_type": "decision.blocked",
      "attempt": 2,
      "attempted_at": "2026-10-30T12:07:02Z",
      "outcome": "failed",
      "status_code": 503,
      "error": "http_status",
      "duration_ms": 412.5,
      "next_attempt_at": "2026-10-30T12:12:02Z",
      "test": false
    }
  ],
  "next_cursor": null
}
```

`error`: `http_status` (non-2xx), `redirect` (3xx), `timeout`, `connection` (DNS, TCP or TLS),
`blocked_address` (the host resolved to an address webhooks may not reach; checked at every attempt,
and the connection goes to the checked address).

## Turning off automatically

An endpoint whose attempts have all failed for 24 hours (counted from the first failure after its
last success, at least five attempts) is turned off with `disabled_reason` `failing`; a receiver
answering 410 (Slack: 403, 404, 410) turns it off at once with `gone`. Its pending deliveries are
cancelled. The console shows the reason; turning it on again starts afresh.

Synthetic example data (AGENTS rule 2).

## Changelog

- 0.1 (2026-10-30, ADR-0048): initial.
