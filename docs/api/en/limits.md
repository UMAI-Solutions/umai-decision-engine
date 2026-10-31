# Limits

| Limit | Evaluation API | AI gateway | When exceeded |
|---|---|---|---|
| Requests per key | 20 per second, bursts up to 40 | same | **429** `rate_limited`, `Retry-After` |
| Invalid keys per address | 20 within 60 seconds | same | **429** `too_many_failed_attempts`, `Retry-After` |
| Requests per month | Your plan's limit, per organization; test keys have their own | same count | **429** `quota_exceeded`, `Retry-After` |
| Content size | 32 000 characters over all messages and artifacts (your plan may set less) | 256 000 characters of checked text per call | **413** `input_too_large` |
| Body size | 512 KB | 4 MB | **413** (from the hosting edge, possibly without a JSON body) or `request_too_large` |
| `timeout_ms` | 50–60 000 ms | — | **422** `invalid_request` |
| `request_id` | 128 characters | `X-Umai-Request-Id`: 128 characters (longer is cut) | **422** `invalid_request` |

The numbers are those of UMAI's hosted service today. A self-hosted installation or your
agreement may set others; the error bodies always tell you the limit that applied.

## Rate limits

Each key has a budget of requests per second that refills continuously, with a burst allowance
on top. Above it, the request is refused with **429** `rate_limited` and a `Retry-After` header
(seconds); nothing is checked and the request does not count toward your quota. Spread load
over time rather than over more keys: rate limits protect the service for every customer.

## Too many invalid keys

An address that presented 20 invalid keys within 60 seconds is refused, with any key, until
`Retry-After` (**429** `too_many_failed_attempts`). See
[Authentication](authentication.md#too-many-invalid-keys).

## Monthly quota

Your plan sets how many requests your organization may make per calendar month (UTC): each
admitted `POST /v1/evaluate` call and each AI gateway call (Chat Completions, Responses,
embeddings) counts once. `GET /v1/models` never counts, and refused requests (401, 403, 413,
422, 429) never count. Live and test keys have separate limits. The console shows your plan and
this month's usage.

When the month's requests are used, every request is refused before anything is checked:

```json
{
  "detail": {
    "error": "quota_exceeded",
    "detail": "this organization used its 10000 live requests for the month; the quota resets at 2026-11-01T00:00:00+00:00",
    "limit": 10000,
    "used": 10000,
    "environment": "live",
    "resets_at": "2026-11-01T00:00:00Z"
  }
}
```

The gateway answers in OpenAI's error shape with `code: quota_exceeded`; both carry
`Retry-After` until the first second of the next month. Your own failure behaviour applies: UMAI
does not let content through unchecked. Talk to us before you reach the limit if you need more.

## Content size

The evaluation API adds up the characters (Unicode code points) of every message and of every
artifact's `content` and `payload_summary`: everything it can scan. Above your limit the request
is refused, nothing is evaluated and nothing is cut:

```json
{
  "detail": {
    "error": "input_too_large",
    "detail": "the content has 41250 characters; this deployment accepts at most 32000",
    "limit": 32000,
    "size": 41250,
    "unit": "characters"
  }
}
```

Send only what needs checking (the guardrail scans the last message by default; older history
adds size but is not scanned), and check long documents in parts.

The gateway checks the whole conversation on every call, so its limit is larger; a message it
has already scanned is not scanned again, so a long conversation stays fast.

## Retrying

| Answer | Retry? |
|---|---|
| 429 `rate_limited`, `too_many_failed_attempts` | Yes, after `Retry-After` seconds |
| 429 `quota_exceeded` | No, not before `resets_at` |
| 502, 503, 504 | Yes, with exponential backoff and jitter, a few times |
| Other 4xx | No: fix the request or the key first |
| 200 with `degraded: true` | The decision stands; retry only if `errors[].retryable` and the call is worth the cost |

A retried evaluation is a new evaluation: it gets a new `event_id` and counts toward your quota.
