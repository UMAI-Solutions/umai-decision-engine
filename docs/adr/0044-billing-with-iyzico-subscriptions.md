# ADR-0044 — Billing for UMAI Cloud with iyzico subscriptions: our record decides the plan, iyzico reports payments

- **Status:** Accepted (2026-10-30)
- **Date:** 2026-10-30
- **Deciders:** product owner (iyzico chosen for monthly subscriptions; plan approved with
  decisions D1–D9, 2026-10-30); engineering
- **Builds on:** ADR-0019 (editions as entitlements), ADR-0025 (tenant store, RLS), ADR-0028
  (metering and billing), ADR-0029 (roles: `billing`), ADR-0033 (organizations), ADR-0043 (plans
  and quotas in the tenant snapshot)
- **Supersedes in part:** ADR-0028 §5 for UMAI Cloud (the `stripe` sink): the cloud provider is
  iyzico. ADR-0028 §4 (our signed record is the entitlement authority; the provider is a feed)
  holds and is implemented here. Self-hosted stays without a billing provider.
- **Answers:** launch plan P4 (billing provider) for UMAI Cloud
- **Facts:** [`reference/iyzico-subscriptions.md`](../reference/iyzico-subscriptions.md) (verified 2026-10-30)

## Context

The product owner chose iyzico for monthly subscriptions; UMAI's merchant account is awaiting
iyzico's approval, sandbox accounts are self-service. The reference check found constraints that
shape the design: subscriptions are card-only and NON3D (no 3-D Secure, the first charge
included); the hosted checkout form exists, its browser callback is not proof of payment and must
be confirmed with a server call; webhooks carry reference codes only and are delivered a few times
(every 15 minutes, about 3 tries) behind an `X-IYZ-SIGNATURE-V3` HMAC that must be enabled per
merchant; a plan's price cannot change; a plan change works only within one product and interval
and creates a new subscription reference (`parentReferenceCode` stays); no call is idempotent;
customers are keyed by e-mail across the merchant account; iyzico handles no VAT and issues no
invoice. Cancel timing, downgrade semantics, when `UNPAID` is set and automatic dunning are not
documented.

UMAI already has editions on tenants, plans with limits in the signed tenant snapshot, and a
`PUT /v1/tenants/{id}/plan` that republishes them (ADR-0043).

## Decision

1. **D1 — Authority.** iyzico is the payment provider for UMAI Cloud subscriptions. A tenant's
   edition changes only through our subscription record and its state machine; iyzico's
   statuses, callbacks and webhooks are inputs that make us re-read and decide (AGENTS rule 3).
2. **D2 — Hosted checkout form only.** Card data never reaches UMAI (no direct NON3D initialize).
   The form's callback goes to a public console route (`/billing/return`): the browser POST
   arrives without the session cookie (`SameSite=Lax`), so the route confirms the checkout
   server-side by its token through the policy service and redirects to the Billing page.
3. **D3 — Paid plans are data.** The plans file (contract entitlements 0.2) gains the paid
   edition `pro` with its `billing` block (`price`, `currency`, `interval`); the deployment maps
   each paid edition to its iyzico pricing-plan reference (`UMAI_DE_BILLING_PLAN_REFS`, per
   environment). `python -m umai_decision_engine.billing.cli sync` creates the product and plans
   in iyzico from the file and prints the references. A new price is a new plan (iyzico rule).
4. **D4 — Records.** Tenant-store migration 0009: `billing_accounts` (the organization's billing
   details: company, tax number and office, address, contact, e-mail, +90 phone), `subscriptions`
   (one per tenant: references, status, period end, grace, retries), `billing_checkouts` (open
   hosted forms by token) and the append-only `billing_events` (deduplicated on the provider's
   event id), all with RLS; and the global `billing_links` table that maps a provider subscription
   reference to its tenant (no personal data), because a webhook names no tenant. No national
   identity number is stored: iyzico requires one and documents that a placeholder is accepted
   (`11111111111`); the tax number (VKN) is what invoices need for organizations.
5. **D5 — Sync.** One public path, `/hooks/iyzico` (Caddy → policy service, the only route of the
   policy service reachable from outside), verifies `X-IYZ-SIGNATURE-V3` in constant time,
   deduplicates on `iyziReferenceCode`, resolves the tenant through `billing_links` and re-reads
   the subscription from iyzico. A reconciliation loop (default every 15 minutes) re-reads every
   subscription that is not in a final state. A status change sets the tenant's edition with
   `set_plan` (overrides kept), which republishes its snapshot (ADR-0043).
6. **D6 — Payment failure.** A failed order moves the subscription to `past_due` with a grace
   period (default 14 days, ADR-0028 §4) and a retry schedule (days 1, 3 and 7 after the failure,
   `POST /v2/subscription/operation/retry` with the order reference, never retried blindly on a
   timeout). A successful payment ends `past_due`. When the grace period ends unpaid, the
   subscription ends and the tenant falls back to Community limits. Evaluation never stops.
7. **D7 — Plan changes.** Starting a paid plan takes effect when the first payment is confirmed.
   An upgrade between paid plans uses iyzico's `upgrade` with `NOW`, a downgrade with
   `NEXT_PERIOD`. A cancellation keeps the paid edition until the end of the paid period, whatever
   iyzico's cancel timing is. Monthly plans only, for now.
8. **D8 — Who.** `owner` and `billing` may start, change and cancel a plan and edit billing details
   (`billing.manage`); `owner`, `admin` and `billing` see the billing page (`billing.read`).
9. **D9 — Invoices later.** Every successful payment is a `billing_events` row with amount and
   currency, enough for an e-Arşiv / e-Fatura integrator to be added as an adapter once the company
   chooses one. VAT is part of the price the plan states.

## Alternatives considered

| Option | Why not |
|---|---|
| Stripe (ADR-0028 §5) | The product owner chose iyzico, a Turkish payment institution for TRY card payments to UMAI's Turkish entity; Stripe stays possible behind the same service if UMAI Cloud sells through another entity |
| Direct NON3D initialize with card fields on our page | Raw card data on our servers (PCI DSS scope) for no gain |
| Trust the callback or the webhook body | Neither proves payment; iyzico says to confirm with a separate call |
| Let iyzico's subscription status decide the edition | A provider outage or an undocumented status change would change customers' limits; our record decides (ADR-0028 §4) |
| Look up a webhook's tenant with the owner role across tenants | Bypasses RLS on the request path; a global reference table holds no personal data |
| Store the contact's TCKN | National identity data we do not need: invoices to organizations use the VKN |

## Consequences

- Contracts: new `billing` 0.1; entitlements 0.2 (edition `pro`, the plan's `billing` block);
  organizations 0.4 (edition `pro`).
- Code: `billing/` (iyzico client with `IYZWSv2` signing and explicit timeouts, the service with its
  state machine, reconciliation, the CLI), policy-service routes under `/v1/billing` and
  `/hooks/iyzico`, migration 0009, settings `UMAI_DE_BILLING_*` and `UMAI_DE_IYZICO_*`.
- Console: a Billing page under Organization and the public return route.
- Operations: sandbox keys now; subscriptions and the V3 webhook signature must be enabled by
  iyzico for each merchant; the live merchant must have no 3-D Secure obligation; the webhook URL
  is set in iyzico's panel (merchant notifications and subscription notifications).
- Company: prices and their VAT, the e-invoicing integrator, the distance-sales contract and the
  other pages iyzico's approval asks for.

## Validation

- Against a fake iyzico (tests): checkout → confirmed → `pro` in the snapshot; a forged webhook
  signature is refused; a repeated webhook is applied once; failure → `past_due` → retry → paid;
  grace ends → Community; cancel keeps `pro` until the period end; plan change; reconciliation
  repairs a missed webhook; roles.
- In the sandbox, once keys exist: the signature order of `X-IYZ-SIGNATURE-V3`, body-less GET
  signing, cancel timing, renewals and webhooks with a `DAILY` plan.
