# ADR-0028 — Metering, entitlements and billing: our own usage ledger, Redis hot counters, a billing adapter (Stripe in cloud, none or Lago self-hosted)

- **Status:** Proposed (awaits product-owner approval; the billing provider is a commercial choice)
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner
- **Builds on:** ADR-0018 (editions, quotas), ADR-0019 §3–4 (signed entitlements, 413/429 semantics), ADR-0025 (tenant-scoped stores), ADR-0030 (outbox, decision events)
- **Facts:** [`reference/usage-billing-providers.md`](../reference/usage-billing-providers.md) (verified 2026-10-17)
- **Answers:** Q23 (counter store) in part; feeds Q20

## Context

Nothing is metered today: no usage counter, no quota, no size limit, no usage endpoint
(ADR-0018 "what the engine does today"). A stolen key costs nothing to abuse (threat model
5.2). ADR-0019 fixed the semantics (429 at the quota, 413 above the size limit, never a
decision for unevaluated content) and left the store and the provider open. The reference check
shows every billing provider wants idempotent, batched events and none can enforce our quota
rule; numeric entitlements exist natively only in Lago; Stripe has no EU residency option;
Orb and Metronome are mid-acquisition.

## Decision

1. **UMAI owns the usage facts.** An append-only, tenant-scoped `usage_events` table (RLS,
   ADR-0025): `event_id` (= the evaluation's `event_id`, the idempotency key), `tenant_id`,
   `occurred_at`, `meter`, `quantity`, `key_id`, `environment` (`live`/`test`), `bundle_id`,
   `bundle_version`, `decision`, `degraded`. Written through the same outbox as the decision
   event (ADR-0030), so there is one source for dashboards, quotas, exports and invoices.
2. **Meters:** `evaluations` — every admitted `POST /v1/evaluate` (failure-policy decisions
   count, rejected 4xx do not; ADR-0019 §4); `input_units` — the request's content size in the
   unit decided by Q19, recorded from day one, billed only if a plan needs it; later
   `l2_analyses`. Keys with `environment = test` are metered separately, never billed, and have
   their own small quota (proposed 1,000 evaluations/month).
3. **Hot counters in Azure Managed Redis, ledger in PostgreSQL Flexible Server (Q23).** Admission
   increments `{tenant}:usage:{meter}:{YYYY-MM}` atomically (hash-tagged so scripts stay in one
   cluster slot, ADR-0031 §7) (`INCR`, TTL past month end) and compares with
   the entitlement's `monthly_requests`; p95 is measured by the admission benchmark. The ledger
   reconciles the counters hourly; drift is a metric. If Redis is unavailable the configured
   behaviour of ADR-0019 §4 applies (default: admit, record the failure) — a billing control
   never blocks a security decision. Single-node self-hosted installs use an in-process counter
   backed by the local ledger.
4. **Entitlements stay our signed record** (ADR-0019 §3). The billing provider is a **feed**: its
   webhooks (subscription created/changed/cancelled, payment failed/recovered) update the tenant
   record in the control plane, which publishes a new signed entitlement snapshot. Payment
   failure proposes: warnings for 14 days, then Community limits, never an abrupt stop of
   evaluation (consistent with the Q24 recommendation for licences).
5. **`UsageSink` adapter with three implementations.** `stripe` for UMAI Cloud (Billing Meters
   for `evaluations` as a `sum` meter via the high-throughput stream API, Entitlements for
   boolean features, invoices and the customer portal); `none` for self-hosted (local ledger,
   `GET /v1/usage`, CSV/JSON export for contract true-ups — no phone-home, ADR-0019); `lago`
   optional (AGPL-3.0, EU endpoint, numeric entitlements) for customers who want in-region
   billing or if Stripe's residency position becomes a blocker for UMAI itself. Metronome is
   re-evaluated at contract time if prepaid credits or commitments are sold.
6. **Reconciliation gate.** A monthly job asserts Σ `usage_events` = `GET /v1/usage` = the
   provider's invoice-line quantity per tenant; drift above 0.1 % raises an alert and keeps the
   invoice in draft (Stripe invoices are finalised only after the check).
7. **Abuse and spend signals.** Per-key hourly rate against a 7-day baseline; a spike notifies
   the tenant's admins (stolen-key scenario) and is an audit event. Enterprise tenants can set
   spend or volume alerts. The console shows usage with warnings at 80 % and 100 % (ADR-0019).
8. **Cost per tenant.** Infrastructure metrics carry the tenant id as a low-cardinality label
   where safe (counters), so cost attribution is possible without content.

## Alternatives considered

| Option | Why not |
|---|---|
| Enforce quotas in the billing provider | None of them implements "429 and never a decision"; couples the request path to an external API; absent self-hosted |
| Postgres-only counters | A hot row per tenant per month under concurrent replicas; Redis `INCR` is the measured, cheaper path — revisited if the benchmark says Postgres suffices at our scale |
| Orb or Metronome first | Orb is being acquired by Adyen, Metronome was just absorbed by Stripe; both are usable through the adapter later |
| Build invoicing and payments ourselves | Not our product; PCI scope |
| No metering in self-hosted | Enterprise contracts are "flexible by contract" (ADR-0018) and need true-ups; the local ledger and export cost little |
| Stripe Entitlements as the entitlement authority | Boolean only, and AGENTS rule 3: our code authorizes from our signed record; provider state is an input |

## Consequences

- Contracts: `usage` 0.1 (ledger record, `GET /v1/usage` shape), evaluation API 0.6 error bodies
  (`429 quota_exceeded`, `429 rate_limited`, `413 input_too_large`), entitlements 0.1 gains
  billing state fields.
- Code: `tenancy/quota.py` (Redis counter, in-process fallback), `usage/` (ledger via outbox,
  reconciliation job, sinks), control-plane webhook endpoints for the provider, console usage
  page.
- Operations: Azure Managed Redis per region (also used by rate limiting and sessions; not the
  retiring Azure Cache for Redis), a Stripe account per legal entity, webhook secrets in Key Vault (ADR-0027); Stripe appears in the
  sub-processor list as processing billing metadata in the US.
- Open: Q19 (what `input_units` counts), Q20 (grace margin for Community), Q22 (export targets).

## Validation

- Readiness M1 and ADR-0019's quota tests: request `limit + 1` → 429, nothing evaluated;
  `limit` reached exactly once under concurrency within the documented overshoot.
- Redis-down test: configured behaviour applied, failure recorded, decision still made.
- Test-environment keys never count toward the live quota or appear on an invoice.
- Reconciliation on a test tenant over one period: three numbers equal; the invoice line is the
  external artifact (verification plan §2).
- Anomaly test: a synthetic spike produces the notification and the audit event.
- Revisit if the admission benchmark shows Redis is unnecessary, or when credits/commitments
  enter pricing (Metronome).
