# Usage metering and billing providers

**Last verified:** 2026-10-17 · **Used by:** ADR-0028 (metering, entitlements, billing), ADR-0019 (quotas) ·
Labels: see [README](README.md). **Vendor claims** from official docs and pricing pages unless
marked otherwise.

## Comparison

| | Stripe Billing (Meters) | Orb | Metronome | Lago (open source) |
|---|---|---|---|---|
| Ingestion | `POST /v1/billing/meter_events`, one event per call, `identifier` = idempotency key; 1,000 calls/s per account; high-throughput v2 `meter_event_stream` with session token: 10,000 events/s, batched; timestamps ≤ 35 days past, ≤ 5 min future | `POST /ingest`, ≤ 500 events per request, `idempotency_key` required, dedup within a grace period (length *unverified*); 10,000 events/min default; `backfill_id` for history | `POST /v1/ingest`, ≤ 100 events, `transaction_id` = idempotency key, **34-day dedup window**, backdating ≤ 34 days; rate limits not published | `POST /api/v1/events` and `/events/batch` (≤ 100, all-or-nothing); 500 events/s per organization; dedup by `transaction_id` |
| Aggregations | **sum, count, last** only | count, unique, sum, max, min, average, custom SQL | per-property aggregates; SQL billable metrics; exact list *unverified* (docs behind login) | count, count unique, latest, max, sum, weighted sum |
| Entitlements / limits | Boolean features only (Feature → ProductFeature → ActiveEntitlement, webhook, lookup API); no numeric quotas | No documented product (spend alerts only) | No: "entitlement state is managed within your system"; payment-threshold webhooks | **Yes**: feature gates, privileges (boolean / integer / string / select), allowances, quotas; per-plan and per-subscription overrides; listed in the Premium matrix — OSS inclusion *unverified* |
| Self-hosting / licence | No | No; being acquired by Adyen (announced 2026-06-11) | No; **owned by Stripe since 2026-01** | **Yes**: Postgres + Redis (ClickHouse optional); **AGPL-3.0**; "Lago Premium" (cloud or self-hosted, quote) adds SSO, audit trail, RBAC, dunning, API-key rotation |
| Pricing | Pay-as-you-go **0.7 % of billing volume**; monthly plans $620 (≤ $100k) … $5,750 (≤ $1M) + 0.67 % overage; Meters included up to 100M events/month; card fees separate | $6 per credit, ≈ 0.60 % of billing volume + ingestion credits | **0.8 % of billing volume + $0.04 per 1k events** (startup tier); custom above | OSS $0; Premium quote only |
| Python SDK | `stripe` (official) | `orb-billing` (official, async) | `metronome-sdk` (official) | `lago-python-client` (MIT, sync) |
| Compliance claims | PCI DSS Level 1, SOC 1/2 Type II, SOC 3, EU-US DPF | SOC 2 Type II, SOC 1 | SOC 2 Type II, SOC 1 Type 2 | SOC 2 Type 2 (hosted) |
| EU residency | **No customer-selectable residency**: data may be handled "in any country where Stripe operates, including the US"; EU entity Stripe Payments Europe Ltd | AWS-hosted; EU region *unverified* | EU region *unverified* | **Yes**: `api.eu.getlago.com` and `api.getlago.com`; self-hosted anywhere |

## Consequences for the design (ADR-0028)

1. **Own the facts.** Every provider wants an idempotency key and small batches, and none of
   them can enforce *our* quota semantics (ADR-0019: 429 at the limit, never a decision). An
   append-only `usage_events` table in the regional store plus one `UsageSink` adapter per
   provider fits all four and keeps self-hosted installs provider-free.
2. **Cloud SaaS:** Stripe is the pragmatic default because payments and invoicing are needed
   anyway; Meters cover sum/count, Entitlements cover boolean gates. Stripe steers new
   usage-based integrations toward Metronome (credits, commitments); re-check packaging and
   price at contract time.
3. **Numeric quotas exist natively only in Lago.** Our quota and prompt-size enforcement come
   from our own counters; provider state is a feed into our signed entitlement record (AGENTS
   rule 3: code authorizes).
4. **Residency:** billing data (company, contacts, invoices) is account metadata, not content;
   Stripe's lack of EU residency is a sub-processor disclosure, not a blocker. Lago's EU
   endpoint or self-hosting is the answer if a customer requires even billing metadata in
   region.
5. **Lock-in watch:** Orb is mid-acquisition and Metronome was just absorbed; keep the adapter
   boundary thin.

## Sources (accessed 2026-10-17)

Stripe: docs.stripe.com/billing/subscriptions/usage-based/recording-usage-api ·
/billing/subscriptions/usage-based/meters/configure · /billing/entitlements ·
/billing/subscriptions/usage-based/compare-metronome · stripe.com/billing/pricing ·
docs.stripe.com/security · stripe.com/legal/privacy-center · stripe.com/newsroom/news/stripe-completes-metronome-acquisition.
Orb: docs.withorb.com/api-reference/event/ingest-events · /events-and-metrics/event-ingestion ·
withorb.com/pricing · withorb.com/security · adyen.com/press-and-media (Orb acquisition).
Metronome: docs.metronome.com/api-reference/usage/ingest-events · /api-reference/idempotency ·
/launch-guides/prepaid-credits · metronome.com/pricing.
Lago: getlago.com/docs/api-reference/events/usage · /events/batch · docs.getlago.com/api-reference/rate-limits ·
getlago.com/docs/guide/billable-metrics/aggregation-types · /guide/entitlements · getlago.com/pricing ·
github.com/getlago/lago/blob/main/LICENSE · getlago.com/blog/open-source-licensing-and-why-lago-chose-agplv3 ·
getlago.com/docs/guide/security/soc.
SDKs: pypi.org/project/stripe · orb-billing · metronome-sdk · lago-python-client.
