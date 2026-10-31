# ADR-0048 — Outbound webhooks and Slack: events from stored records, derived signing secrets, a guarded sender in the policy service

- **Status:** Proposed
- **Date:** 2026-10-30
- **Deciders:** engineering (Cloud SaaS checklist items "webhooks" and "ready-made integrations
  (Slack…)"); product owner to accept
- **Builds on:** ADR-0005 (no raw content beyond L0), ADR-0022 (policy service), ADR-0025 (tenant
  store, RLS), ADR-0029 (roles), ADR-0032 amendment (keys derived from a deployment master key on
  single-VM installs), ADR-0041 (decision events, usage ledger), ADR-0043 (plans and quotas),
  ADR-0044 (billing events)
- **Contract:** [webhooks 0.1](../contracts/webhooks.md) · **Facts:**
  [Slack incoming webhooks](../reference/slack-incoming-webhooks.md) (verified 2026-10-30)

## Context

Organizations want to hear about what UMAI did without watching the console: a burst of blocked
requests, a quota running out, a failed payment. The usual answer is outbound webhooks (signed
HTTPS POSTs to a URL the customer owns) and a Slack integration. The facts we build on: decision
events, the usage ledger and billing events are already stored per tenant with RLS, append-only,
and contain no content (ADR-0041, ADR-0044); the policy service is the one process per stack that
reads them and already runs background loops (retention, billing reconciliation); Slack incoming
webhooks take `text` and `blocks`, sign nothing (the URL is the credential), answer 200 `ok`, and
answer 403/404/410 for a revoked or archived target.

Sending HTTP requests to customer-chosen URLs from inside our network is a server-side request
forgery risk: on Azure, `168.63.129.16` and `169.254.169.254` answer any VM, and the policy service
can reach Postgres and the engines.

## Decision

1. **Where.** The policy service sends webhooks, from a worker task in its process (one per stack,
   like billing reconciliation). The request path of the engine and the gateway is untouched: no
   event is produced there and no latency is added.
2. **Sources are the stored records, polled.** Every minute the worker reads, per tenant with an
   enabled endpoint: blocked decision events (`decision_events.action = BLOCK`) in fixed
   aggregation windows (default 5 minutes, aligned, read one minute after the window ends so late
   records are in), the month's request count against the plan's limit (`quota.threshold` at 80 %
   and 100 %), and new `billing_events` (`billing.payment_succeeded`, `billing.payment_failed`,
   `billing.subscription_changed`). A blocked burst becomes **one event per window** with counts, top
   applications, entity types and policies and five sample event ids, never one per call.
3. **Idempotent by key, cursors in memory.** Each event has a deterministic key (the window's start,
   month + environment + threshold + limit, the billing event's id); a message is stored once per
   endpoint and key (unique constraint). The read position lives in memory; after a restart the
   worker reads the last hour again and the keys drop what was already sent. No cursor table.
   An endpoint receives only events that happened after it was created or enabled.
4. **Tables (tenant store migration 0012, RLS).** `webhook_endpoints` (URL encrypted, kind, event
   types, state, failure streak), `webhook_messages` (the outbox: one event for one endpoint, its
   status and next attempt; updatable), `webhook_deliveries` (one row per attempt, **append-only**:
   status code, error code, duration; never the response body). Retention deletes deliveries and
   finished messages after 30 days with the owner role.
5. **Payloads.** Ids, types, codes, counts, amounts and console links only (ADR-0005): no content,
   not even redacted text, entity values, spans or hashes. Every payload is a contract model that
   forbids unknown fields. Generic endpoints get a JSON envelope (`event_id`, `type`,
   `occurred_at`, `organization_id`, `data`, `links`); `slack` endpoints get Slack's `text` + mrkdwn
   `blocks` with links (no buttons: a button needs an interactivity endpoint).
6. **Signing.** `X-Umai-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "t." + body)>`; receivers
   check the timestamp (5 minutes) and deduplicate on `event_id`. The **secret is derived, not
   stored**: `HKDF-SHA256(UMAI_DE_WEBHOOK_MASTER_KEY, info = tenant id, endpoint id, secret version)`,
   shown once at creation and at rotation (the API never shows it again, although the service can
   re-derive it to sign). Rotation increments the version; the previous secret signs alongside for
   24 hours. Slack endpoints are not signed.
7. **URLs encrypted at rest.** An endpoint URL can carry a credential (Slack's always does), so it is
   stored AES-256-GCM-encrypted with a key derived from the same master key (associated data: tenant
   and endpoint ids), masked in every answer (scheme, host, last four characters) and never logged.
   Without `UMAI_DE_WEBHOOK_MASTER_KEY` outside development, webhooks are unavailable (routes answer
   503 `webhooks_unavailable`, the worker does not run); development uses a fixed development key.
8. **SSRF guard.** `https` only, no user info, port 443 or ≥ 1024; at registration a literal or
   resolving address must be public. At every attempt the sender resolves the host itself, refuses
   any non-global address (private, loopback, link-local, carrier-grade NAT, multicast, reserved,
   unspecified; IPv4-mapped, NAT64, 6to4 and Teredo forms) and the cloud metadata/host addresses
   (`168.63.129.16`, `169.254.169.254`, `fd00:ec2::254`), then **connects to the checked address**
   (a pinned network backend; TLS still verifies the host name), so DNS rebinding cannot swap it.
   No redirects, no proxies from the environment, 3 s to connect and 10 s per attempt, the response
   body is never read.
9. **Delivery.** At least once, unordered. Retries after 1 m, 5 m, 30 m, 2 h and 6 h (six attempts).
   Slack endpoints get at most one message per second. An endpoint whose attempts have all failed
   for 24 hours (and at least five attempts) is turned off with reason `failing`; 410 (Slack: 403,
   404, 410) turns it off at once with `gone`; its pending messages are cancelled and the console
   shows why. A test event is sent synchronously from the console, never retried, and does not
   count towards turning off.
10. **Who.** `webhook.read` (owner, admin, operator) and `webhook.manage` (owner, admin; a person is
    named). At most 10 endpoints per organization (setting).

## Alternatives considered

| Option | Why not |
|---|---|
| Emit events on the request path (engine, gateway) into a queue | Adds work and a failure mode to every evaluation; the records already exist and are read anyway |
| One webhook per blocked decision | An attack of thousands of calls floods the receiver and Slack's 1 message/s limit; the console holds the detail |
| Store the signing secret (plain or encrypted) | A database copy or backup would hold every secret; derivation needs nothing stored and matches ADR-0032's single-VM tenant keys. UMAI Cloud can wrap the master key in Key Vault (ADR-0027) without changing the scheme |
| Store only a hash of the secret, like API keys | We must sign with it, so it has to be recoverable |
| Cursor table per tenant and source | Deterministic keys make re-reading harmless; one table and one write per tick fewer |
| Resolve, check, then let the HTTP client resolve again | DNS rebinding: the second answer can be `169.254.169.254` |
| Follow redirects after re-checking | A redirect is a second, unreviewed target; receivers can give the final URL |
| Slack app with OAuth and `chat.postMessage` | A Slack app, its review and token storage for one notification channel; incoming webhooks are what Slack offers for this and need no app on our side |

## Consequences

- Contract `webhooks` 0.1; tenant-store migration 0012; `control_plane/webhooks.py` (service and
  worker), `webhooks/` (signing, encryption, SSRF guard, sender, Slack format); routes under
  `/v1/webhooks`; actions `webhook.read`, `webhook.manage`; settings `UMAI_DE_WEBHOOK_*`,
  `UMAI_DE_WEBHOOKS_*` and `UMAI_DE_CONSOLE_URL` (links); `umai.sh` generates the master key on the
  single VM.
- Console: Organization → Webhooks (add endpoint with kind and events, secret shown once, test,
  enable/disable, delivery log).
- The policy service now makes outbound HTTPS requests: its egress must allow them (single VM:
  nothing to change; UMAI Cloud: through the NAT gateway, never through a private endpoint).
- Losing or changing the master key changes every signing secret and makes stored URLs unreadable:
  it is backed up with the other secrets and rotated only with re-registration.
- Known limits: a decision event recorded more than one minute after its window ended is not
  reported; ordering is not guaranteed; the worker's in-memory position means one process per stack
  (as billing).

## Validation

- Tests with a fake receiver: signatures verify with the documented code; retries follow the
  schedule; turning off after the failing streak and on 410; SSRF refusals for private, loopback,
  link-local, metadata, mapped and rebinding answers, and for redirects; payloads parse as the
  contract's models and carry no content; quota thresholds once per month; billing events once;
  roles; tenant isolation of the three tables on Postgres.
- Revisit when UMAI Cloud runs more than one policy-service replica (needs a lease or a queue), or
  when customers ask for per-call events (would need a streaming source, not polling).
