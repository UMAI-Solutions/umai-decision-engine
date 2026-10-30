# Contract: billing (paid plans, checkout, subscription state)

**Version:** 0.1 · **Producer → consumer:** console ↔ policy service (`/v1/billing…`), iyzico →
policy service (`/hooks/iyzico`) · **Decisions:**
[ADR-0044](../adr/0044-billing-with-iyzico-subscriptions.md), [ADR-0043](../adr/0043-plans-and-monthly-quotas.md),
[ADR-0028](../adr/0028-metering-entitlements-and-billing.md) §4 · **Provider facts:**
[iyzico subscriptions](../reference/iyzico-subscriptions.md)

An organization buys a paid plan (an edition whose plan has a `billing` block,
[entitlements](entitlements.md) 0.2) in iyzico's hosted form. Our subscription record decides
the edition; iyzico's callback and webhooks only make the policy service re-read the subscription.
Card data never reaches UMAI.

## Roles

`billing.read`: owner, admin, billing. `billing.manage` (buy, change, cancel, billing details):
owner, billing. Completing a checkout is an operator call (the console's public return route).

## `GET /v1/billing`

```json
{
  "contract_version": "0.1",
  "tenant_id": "acme",
  "edition": "pro",
  "provider": "iyzico",
  "can_manage": true,
  "subscription": {
    "status": "active",
    "edition": "pro",
    "provider": "iyzico",
    "started_at": "2026-10-30T12:00:00Z",
    "current_period_end": "2026-11-30T12:00:00Z"
  },
  "account": {
    "company_name": "Acme Teknoloji A.Ş.",
    "tax_number": "1234567890",
    "tax_office": "Kadıköy",
    "contact_name": "Ayşe",
    "contact_surname": "Demir",
    "email": "billing@example.com",
    "phone": "+905551112233",
    "address": "Örnek Mah. Deneme Sok. No: 1",
    "city": "İstanbul",
    "country": "Turkey"
  },
  "plans": [
    {
      "edition": "pro",
      "price": "1499.00",
      "currency": "TRY",
      "interval": "MONTHLY",
      "limits": { "monthly_requests": 100000, "monthly_test_requests": 10000, "max_input_chars": 32000 },
      "available": true
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `provider` | `iyzico` when the deployment sells plans, else `none` (nothing can be bought) |
| `subscription.status` | `pending` (confirmed, first payment not in), `active` (paid), `past_due` (a payment failed; `grace_until`, `next_retry_at`, `last_payment_error`), `canceled` (cancelled; the paid edition stays until `cancel_at`), `ended` (the tenant is on Community limits again) |
| `plans[].available` | `false` when the deployment has no provider plan for the edition, or the provider plan's price, currency or interval differs from the plans file |

Synthetic example data (AGENTS rule 2).

## `PUT /v1/billing/account`

Body: `account` as above (all fields but `zip_code` required; `country` defaults to `Turkey`;
`phone` `+90` and ten digits; `tax_number` 10 or 11 digits). Answer: the view. Required before a
checkout. iyzico also requires a national identity number for its customer record; UMAI sends
the placeholder iyzico documents as accepted and never stores one.

## `POST /v1/billing/checkout`

Body `{"edition": "pro"}` → `{checkout_id, form_content, expires_at}`. `form_content` is iyzico's
script for the hosted form, rendered into `<div id="iyzipay-checkout-form" class="responsive">`.
The form returns the buyer to the console's `/billing/return?org=<tenant>&checkout=<checkout_id>`
with a `token`. Errors: 409 `billing_account_required`, 409 `already_subscribed` (use
`/v1/billing/plan`), 422 `plan_not_available`, 503 `billing_unavailable`, 502 `provider_error`.

## `POST /v1/billing/checkouts/{checkout_id}/complete` (operator)

Body `{"token": "…"}`, header `X-Umai-Tenant`. The policy service asks iyzico for the form's
result by token; only a result for this checkout counts. Answer:
`{tenant_id, checkout_id, status, edition}` with `status` `active` (the tenant is on the paid
edition; limits reach the engines within one snapshot poll), `pending` or `failed`. Completing
twice answers the same result.

## `POST /v1/billing/plan`

Body `{"edition": "<paid edition>"}`: moves an active subscription to another paid plan (same
interval). More expensive: now (charged at once by the provider); cheaper: from the next period.
To stop paying use `/cancel`.

## `POST /v1/billing/cancel`

Cancels at the provider; the subscription becomes `canceled` and keeps its paid edition until the
end of the paid period (`cancel_at`), then `ended`.

## `POST /hooks/iyzico` (public)

iyzico's subscription notifications: `{orderReferenceCode, customerReferenceCode,
subscriptionReferenceCode, iyziReferenceCode, iyziEventType, iyziEventTime}`. Accepted only with a
valid `X-IYZ-SIGNATURE-V3`; a repeated `iyziReferenceCode` is applied once; the body only makes the
policy service re-read the subscription. Answers 200 `{"received": true}`, 401 for a bad
signature, 200 for an unknown subscription (logged, so iyzico stops resending).

## State machine

| From | Event (read from iyzico) | To | Edition |
|---|---|---|---|
| none | checkout confirmed, subscription `ACTIVE` | `active` | paid |
| none | checkout confirmed, `PENDING` | `pending` | unchanged |
| `pending` | an order succeeds | `active` | paid |
| `active`, `canceled` | an order fails | `past_due` (grace 14 days, retries on days 1, 3, 7) | unchanged |
| `past_due` | an order succeeds (retry or renewal) | `active` | paid |
| `past_due` | grace ends unpaid | `ended` (cancelled at the provider) | Community |
| `active` | cancel | `canceled` (`cancel_at` = end of the paid period) | unchanged |
| `canceled` | `cancel_at` passes | `ended` | Community |
| any | provider says `CANCELED` / `EXPIRED` outside our own cancel | `canceled` until the paid period ends | unchanged until then |

## Changelog

- 0.1 (2026-10-30, ADR-0044): initial.
