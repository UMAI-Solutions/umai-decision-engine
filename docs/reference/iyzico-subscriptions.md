# iyzico Subscription (Abonelik) API

**Last verified:** 2026-10-30 · **Used by:** billing (planned) · Labels: see [README](README.md).
**Vendor claims** from docs.iyzico.com unless marked otherwise. All sources below were read on
2026-10-30. Pages have `.md` variants (append `.md`); the index is
[llms.txt](https://docs.iyzico.com/llms.txt). The subscription endpoints sit under "API Reference
(Beta)". The Turkish pages carry hints the English ones lack (NON3D, 160-day retry); both are cited.

## Source keys

| Key | Page |
|---|---|
| AUTH | [HMACSHA256 Auth](https://docs.iyzico.com/en/getting-started/preliminaries/authentication/hmacsha256-auth) |
| ENV | [Live vs Sandbox](https://docs.iyzico.com/en/getting-started/preliminaries/live-vs-sandbox) |
| SBX | [Sandbox](https://docs.iyzico.com/en/getting-started/preliminaries/sandbox) |
| IDEM | [Idempotency](https://docs.iyzico.com/en/getting-started/preliminaries/idempotency) |
| LIM | [Limitler](https://docs.iyzico.com/on-hazirliklar/limitler) |
| SUB | [Subscription](https://docs.iyzico.com/en/products/subscription), [Abonelik](https://docs.iyzico.com/urunler/abonelik) |
| IMPL | [Subscription Implementation](https://docs.iyzico.com/en/products/subscription/subscription-implementation), [Abonelik Entegrasyonu](https://docs.iyzico.com/urunler/abonelik/abonelik-entegrasyonu) |
| PROD | [Subscription Product](https://docs.iyzico.com/en/products/subscription/subscription-implementation/subscription-product) |
| PLAN | [Payment Plan](https://docs.iyzico.com/en/products/subscription/subscription-implementation/payment-plan) |
| TX / TX-TR | [Subscription Transactions](https://docs.iyzico.com/en/products/subscription/subscription-implementation/subscription-transactions) / [Abonelik İşlemleri](https://docs.iyzico.com/urunler/abonelik/abonelik-entegrasyonu/abonelik-islemleri) |
| CUST | [Subscriber Transactions](https://docs.iyzico.com/en/products/subscription/subscription-implementation/subscriber-transactions) |
| WH / WH-TR | [Webhook](https://docs.iyzico.com/en/advanced/webhook) / [Webhook (TR)](https://docs.iyzico.com/ek-servisler/webhook) |
| CARDS | [Test Cards](https://docs.iyzico.com/en/add-ons/test-cards) |
| REF | [Refund & Cancel](https://docs.iyzico.com/en/advanced/refund-and-cancel) |
| FAQ-SUB / FAQ-WH / FAQ-START / FAQ-PAY / FAQ-ERR | FAQ: [Abonelik ve Kart Saklama](https://docs.iyzico.com/sss/abonelik-ve-kart-saklama), [Webhook](https://docs.iyzico.com/sss/webhook), [Başlangıç](https://docs.iyzico.com/sss/baslangic), [Ödeme Alma](https://docs.iyzico.com/sss/odeme-alma), [Hata Mesajları](https://docs.iyzico.com/sss/hata-mesajlari) |
| PY | [iyzipay-python](https://github.com/iyzico/iyzipay-python) `master` = v1.0.46, [PyPI](https://pypi.org/project/iyzipay/) |
| GH | iyzico GitHub issues (**secondary source**), linked where used |

## Environments and authentication

- **Base URLs:** live `https://api.iyzipay.com`, sandbox `https://sandbox-api.iyzipay.com`; only the
  host differs. Live and sandbox keys are different; sandbox keys start with `sandbox-` (ENV). Keys
  and base URL must belong to the same environment, otherwise "API bilgileri bulunamadı"
  (FAQ-START). Panels: `merchant.iyzipay.com`, `sandbox-merchant.iyzipay.com` (FAQ-START).
- **`IYZWSv2` header** (AUTH):
  1. `randomKey`: any per-request random string (docs: timestamp + digits; PY: 8 alphanumerics).
     Send it also as header `x-iyzi-rnd`; without it the API answers `1004` "Random string
     gönderilmesi zorunludur" (FAQ-ERR).
  2. `signature = hex(HMAC-SHA256(key = secretKey, msg = randomKey + uriPath + body))`. `uriPath` is
     the path only (`/v2/subscription/...`), no host, **no query string** (AUTH "uri.path"; PY strips
     `?...` before signing). `body` is the exact JSON string sent; with no body the Postman script
     signs `randomKey + uriPath` only (AUTH).
  3. `Authorization: IYZWSv2 ` + `base64("apiKey:" + apiKey + "&randomKey:" + randomKey + "&signature:" + signature)`,
     one space after `IYZWSv2` (AUTH). Plus `Content-Type: application/json`.
- **Pitfalls (GH, secondary source):** a signed path that differs from the request path by one
  character (trailing slash before `?`) gives HTTP 401 `errorCode` `8` "Authentication token is not
  verified" ([iyzipay-java#182](https://github.com/iyzico/iyzipay-java/pull/182)); a wrongly cut path
  gives 401 `errorCode` `5` "Authentication failed!" ([iyzipay-php#228](https://github.com/iyzico/iyzipay-php/pull/228)).
  PY sends and signs the literal body `null` on body-less GETs; whether the server expects `null`
  or nothing is **unverified** (test both in sandbox).
- **Envelope:** `status` (`success` | `failure`), `systemTime` (epoch ms), `locale` (`tr` | `en`),
  `conversationId` (echoed if sent; optional on every subscription call, TX). Failures add
  `errorCode` (string) and `errorMessage`. v2 subscription resources return their payload in `data`;
  checkout-form initialize returns `token`, `checkoutFormContent`, `tokenExpireTime` (seconds) at top
  level (TX). The OpenAPI blocks list HTTP 200 and 400 only; a wrong path returns 404
  `{timestamp, status, error, path}` (TX).
- **No idempotency keys.** "Majority of iyzico services have designed non-idempotent";
  `conversationId` is only a correlation value (IDEM).
- **Rate limits** (LIM, subscription table, per minute): product create/update 100, plan list 100,
  plan detail 200, subscription search 100, subscription detail 500, checkout-form result 500. The
  table writes paths as `/api/v2/...` and its columns are misaligned; above the limit:
  `errorCode` `50000` "Request Limit Exceeded" (HTTP status **unverified**).

## Resources and endpoints

All paths relative to the base URL; `*` = required. Dates in responses are epoch ms unless noted.

### Product (PROD)

| Call | Path | Fields |
|---|---|---|
| Create | `POST /v2/subscription/products` | `name*` (unique), `description` → `data{referenceCode, createdDate, name, description, status, pricingPlans[]}` |
| List | `GET /v2/subscription/products` | `page`, `count` (shown as a request body on a GET; send as query, **unverified**) → `data{totalCount, currentPage, pageCount, items[]}` |
| Get | `GET /v2/subscription/products/{productReferenceCode}` | |
| Update | `POST /v2/subscription/products/{productReferenceCode}` | `name*`, `description` |
| Delete | `DELETE /v2/subscription/products/{productReferenceCode}` | only when no plan is attached |

### Pricing plan (PLAN)

| Call | Path | Fields |
|---|---|---|
| Create | `POST /v2/subscription/products/{productReferenceCode}/pricing-plans` | `name*` (unique), `price*` (decimal), `currencyCode*` `TRY` \| `USD` \| `EUR` ("For foreign currency, only non-TRY cards are allowed"), `paymentInterval*` `DAILY` \| `WEEKLY` \| `MONTHLY` \| `YEARLY`, `paymentIntervalCount` (multiplier: `WEEKLY` + 2 = every two weeks), `planPaymentType*` `RECURRING` (only value), `recurrenceCount` (omitted = until cancelled), `trialPeriodDays` |
| Update | `POST /v2/subscription/pricing-plans/{pricingPlanReferenceCode}` | **only** `name*` and `trialPeriodDays`; active subscriptions unaffected |
| Get | `GET /v2/subscription/pricing-plans/{pricingPlanReferenceCode}` | |
| List | `GET /v2/subscription/products/{productReferenceCode}/pricing-plans?page=&count=` | |
| Delete | `DELETE /v2/subscription/pricing-plans/{pricingPlanReferenceCode}` | not while subscriptions or pending updates use it |

A plan belongs to one product. **Plan prices cannot be changed**: create a new plan in the same
product and upgrade subscriptions to it (FAQ-SUB). Plan status: `ACTIVE`.

### Customer / subscriber (CUST)

- **No create endpoint is documented.** A customer is created by subscription initialize and keyed
  by **email**: the same email in another subscription updates that customer's details and keeps the
  same `customerReferenceCode` (CUST). PY also has `POST /v2/subscription/customers` (create) and
  `POST /v2/subscription/customers/delete/{code}` (delete): SDK only, **unverified**.
- `POST /v2/subscription/customers/{customerReferenceCode}`: update ≥ 1 of `name`, `surname`,
  `email`, `gsmNumber`, `identityNumber`, `billingAddress`, `shippingAddress`
  (`{address, zipCode, contactName, city, district, country}`).
- `GET /v2/subscription/customers/{customerReferenceCode}`, `GET /v2/subscription/customers?page=&count=`.
  Response adds `contactEmail`, `contactGsmNumber`, `status` `ACTIVE`.

### Subscription (TX, TX-TR)

| Call | Path | Fields |
|---|---|---|
| Initialize, hosted form | `POST /v2/subscription/checkoutform/initialize` | `callbackUrl*`, `pricingPlanReferenceCode*`, `subscriptionInitialStatus*` `ACTIVE` \| `PENDING`, `customer*{name*, surname*, email*, gsmNumber*, identityNumber*, billingAddress*{address*, contactName*, city*, country*, zipCode}, shippingAddress}`, `locale` (default `tr`), `conversationId` → `token`, `checkoutFormContent` (script; render into `<div id="iyzipay-checkout-form" class="responsive">` or `class="popup"`), `tokenExpireTime` |
| Form result | `GET /v2/subscription/checkoutform/{token}` (`?conversationId=`) | → `data{referenceCode, parentReferenceCode, customerReferenceCode, pricingPlanReferenceCode, subscriptionStatus, trialDays, trialStartDate, trialEndDate, createdDate, startDate, endDate}` |
| Initialize, direct (NON3D) | `POST /v2/subscription/initialize` | same as form minus `callbackUrl` and `locale`, plus `paymentCard*{cardHolderName*, cardNumber*, expireMonth*, expireYear*, cvc*}` (raw card data through our server) |
| Initialize, existing customer | `POST /v2/subscription/initialize/with-customer` | `customerReferenceCode*`, `pricingPlanReferenceCode*`, `subscriptionInitialStatus*`; works only if the customer already has an active subscription |
| Activate | `POST /v2/subscription/subscriptions/{subscriptionReferenceCode}/activate` | for subscriptions started `PENDING` |
| Cancel | `POST /v2/subscription/subscriptions/{subscriptionReferenceCode}/cancel` | "Cancels an active subscription"; immediate vs period end **unverified** |
| Upgrade (any plan change) | `POST /v2/subscription/subscriptions/{subscriptionReferenceCode}/upgrade` | `newPricingPlanReferenceCode*`, `upgradePeriod*` `NOW` \| `NEXT_PERIOD`, `useTrial` (apply the new plan's trial), `resetRecurrenceCount` (true: end date from the new plan's `recurrenceCount`; false: keep the current end date) → `data.referenceCode` = **new** subscription |
| Retry failed payment | `POST /v2/subscription/operation/retry` | `referenceCode*` = the `orderReferenceCode` from the failure webhook |
| Detail | `GET /v2/subscription/subscriptions/{subscriptionReferenceCode}` | paged shape: `data{totalCount, currentPage, pageCount, items[]}`; item: `referenceCode, parentReferenceCode, pricingPlanName, pricingPlanReferenceCode, productName, productReferenceCode, customerEmail, customerGsmNumber, customerReferenceCode, subscriptionStatus, trial*, createdDate, startDate, endDate, orders[]` |
| Search | `GET /v2/subscription/subscriptions` | filters `subscriptionReferenceCode`, `customerReferenceCode`, `pricingPlanReferenceCode`, `parent`, `subscriptionStatus`, `startDate`, `endDate`, `page`, `count` (the docs label them path parameters; PY sends them as a query string) |
| Card update, hosted form | `POST /v2/subscription/card-update/checkoutform/initialize` | `callbackUrl*`, `customerReferenceCode*`, `subscriptionReferenceCode` (then only that subscription's card changes), `locale` → `token`, `checkoutFormContent`, `tokenExpireTime` |

Behaviour:

- A subscription starts `ACTIVE` or `PENDING`. `PENDING`, or `ACTIVE` with a plan trial: the card is
  only validated (1 TL charge, then refunded). `ACTIVE` without trial: the first plan payment is
  charged at initialize (TX). After a trial the subscription starts automatically on the stored card (SUB).
- **Callback is not proof of payment.** The form redirects the buyer to `callbackUrl` and POSTs
  `token` there; the merchant "must check whether the payment has been received with a separate
  request" (form result call) (TX, TX-TR).
- **Plan change rules:** the new plan must belong to the **same product** and have the **same
  `paymentInterval` and `paymentIntervalCount`**; "Use this service for price changes" (TX). There
  is no separate downgrade call: a downgrade is an "upgrade" to a cheaper plan (FAQ-SUB states only
  "upgrade"; downgrade explicitly **unverified**). `NOW`: "the new amount is charged immediately";
  `NEXT_PERIOD`: from the next billing period (FAQ-SUB). Proration is not documented (**unverified**).
  The old subscription becomes `UPGRADED`; `parentReferenceCode` correlates the chain (TX).
- **Retry:** a failed payment can be retried for at most **160 days**; a successful retry sends its
  own webhook (TX-TR only). Retries can also be done in the panel (IMPL, TR). Automatic retries
  (dunning) by iyzico are not documented (**unverified**).
- **Card update:** only via the hosted form; 1 TL validation charge, refunded. A `token` POSTed to
  `callbackUrl` means the card was updated (TX). No retrieve-by-token call is documented for it.
- Subscriptions started on the 29th–31st are charged on the last day of shorter months (FAQ-SUB).
  Time of day of renewals: **unverified**.

### Statuses

| `subscriptionStatus` | Meaning (TX) |
|---|---|
| `ACTIVE` | active, payments collected regularly |
| `PENDING` | stopped/paused; also the initial status that needs `activate` |
| `UNPAID` | a payment could not be collected (after how many failures: **unverified**) |
| `UPGRADED` | replaced by a subscription on another plan |
| `CANCELED` | cancelled |
| `EXPIRED` | period (`recurrenceCount`) ended; no further charges |

Orders (one per billing period): `orderStatus` `WAITING` \| `SUCCESS` \| `FAILED`, with `price`,
`currencyCode`, `startPeriod`, `endPeriod`. Each `paymentAttempts[]` entry: `paymentStatus`
`SUCCESS` (with `paymentId`) \| `FAILED` (with `errorCode`, `errorMessage`), `createdDate`,
`conversationId` (TX).

## Webhooks

- **URLs** (HTTPS required): the first payment that starts a subscription is notified to the
  "İşyeri Bildirimleri" (merchant notifications) URL; later recurring payments to the "Abonelik
  Bildirimleri" (subscription notifications) URL (FAQ-WH). Panel path: Settings > Merchant Settings
  > Merchant (Subscription) Notifications (WH, WH-TR). Which format the first notification uses
  (HPP `CHECKOUT_FORM_AUTH`?) is **unverified**.
- **Subscription payload** (WH): `orderReferenceCode`, `customerReferenceCode`,
  `subscriptionReferenceCode`, `iyziReferenceCode` (unique per notification), `iyziEventType`
  `subscription.order.success` \| `subscription.order.failure`, `iyziEventTime` (13-digit, epoch ms).
  No amount, subscription status or `merchantId` in the body. After a notification "the current status of the
  subscription must be checked" (IMPL, TR).
- **Delivery:** first notification 10–15 s after the payment attempt; repeated every 15 minutes
  until a 2xx; stops "after 3 attempts" (EN) / "3 defa tekrar edecektir" (TR), so 3 or 4 deliveries
  in total (**unclear**) (WH, WH-TR).
- **`X-IYZ-SIGNATURE-V3`**: lowercase hex of HMAC-SHA256 with key `secretKey` over
  `merchantId + secretKey + eventType + subscriptionReferenceCode + orderReferenceCode + customerReferenceCode`
  (code samples in JS, PHP, Java, Node; EN and TR). **Conflict:** the prose lists `SECRET KEY` first,
  then `merchantId`; the code puts `merchantId` first. The samples name the field `eventType`, the
  payload has `iyziEventType` (assumed the same value, **unverified**). `merchantId` comes from our
  configuration. Verify the order in sandbox. `X-Iyz-Signature` and `-V2` are no longer supported (WH).
- **The signature must be enabled per account** by e-mail to `entegrasyon@iyzico.com` (WH, WH-TR, FAQ-WH).

## Sandbox

- Self-service sign-up at `https://sandbox-merchant.iyzipay.com/auth/register`; no e-mail or SMS
  is sent, the OTP is always `123456`; any PNG passes the document step; no password reset (open a
  new account) (SBX, FAQ-START). Keys: Settings > Merchant Settings > API Keys (SBX).
- **Subscriptions must be enabled per merchant.** Sandbox: send the account's e-mail or merchant
  number to `entegrasyon@iyzico.com`. Live: buy the add-on on the panel's "Eklentiler" page, or
  write to `destek@iyzico.com` if it is not listed (IMPL, TR). Without it subscription calls return
  `errorCode` `100001` "Sistem hatası" (GH, **secondary source**:
  [iyzipay-node#127](https://github.com/iyzico/iyzipay-node/issues/127),
  [iyzipay-php#136](https://github.com/iyzico/iyzipay-php/issues/136)).
- **Test cards** (CARDS; any future expiry, any well-formed CVC; live cards are rejected):
  success `5528790000000008` (Halkbank MC credit), `4603450000000000` (Denizbank Visa credit),
  `5890040000000016` (Akbank MC debit); non-Turkish `5400010000000004` (credit, needed for USD/EUR
  plans); failures `4111111111111129` insufficient funds, `4129111111111111` do not honour,
  `4125111111111115` expired card, `4124111111111116` invalid CVC2, `4130111111111118` general error.
- **No documented way to make a later recurring charge fail** (a failing card fails the initial
  1 TL validation). Whether sandbox runs renewals on schedule and sends subscription webhooks is
  not documented (**unverified**; a `DAILY` plan would show it).

## Constraints and commercial facts

| Topic | Fact |
|---|---|
| Payment method | **Credit cards only** (IMPL). Cards stored with the separate card-storage add-on cannot be used; the two add-ons cannot be active at once (SUB TR, FAQ-SUB). Not for marketplace accounts (FAQ-SUB) |
| 3DS | **Not supported**: every subscription transaction, the first included, is NON3D. If the account has a 3DS obligation, subscription requests fail; ask iyzico to lift it (TX-TR, FAQ-SUB). Payments under a 3DS obligation return `5010` (FAQ-ERR); that subscriptions return the same code is **unverified** |
| Customer data | `gsmNumber` must start with **+90** (SMS verification only to Turkish numbers); `identityNumber` (TCKN) required, a dummy value is accepted (FAQ-SUB, FAQ-PAY) |
| Currencies | Plans: `TRY`, `USD`, `EUR` (PLAN). Turkish cards cannot pay in foreign currency (FAQ-PAY, PLAN). Whether a subscription in USD/EUR also needs the Multicurrency add-on (FAQ-PAY, general payments) is **unverified** |
| VAT / KDV | No tax field anywhere in the subscription API; `price` is the amount charged. VAT handling is not documented (**unverified**) |
| Invoices | iyzico documents no invoicing to the end customer (no e-Arşiv / e-Fatura) in any page read (**unverified**, consistent with the belief that a GİB integrator is needed) |
| Add-on fee | "free for the first 3 months, and after that, it is priced at 199TL" (SUB, EN and TR). Billing period (monthly?) and VAT inclusion **unverified** |
| Transaction fees | General iyzico pricing: commission "starting from 4.29%" (corporate) plus 0.25 TL per successful transaction, no fixed fees; no effective date on the page ([pricing](https://www.iyzico.com/en/help-center/general/pricing)). Subscription-specific rates **unverified** |
| Refunds | No subscription refund call. Generic refunds within 365 days: `POST /v2/payment/refund` `{paymentId*, price*}` or `POST /payment/refund` `{paymentTransactionId*, price*}` (REF). That they apply to subscription order payments (via `paymentAttempts[].paymentId`) is **unverified**. Cancel does not refund |
| Account | API access only for company accounts, not individual ones; one domain per account; API restricted if no successful payment within 30 days of completing the application; go-live check: a 5–10 TL real payment, then refund (FAQ-START) |
| Website requirements | Not documented on docs.iyzico.com (**unverified**). Integrator blogs list distance sales contract, pre-information form, return/cancellation and privacy policies, contact details and card logos (**secondary source**: [Ticimax](https://www.ticimax.com/blog/iyzico-sanal-pos-basvurusu-nasil-yapilir), [IdeaSoft](https://www.ideasoft.com.tr/iyzico-nedir-nasil-kullanilir/)) |

## Python SDK

`iyzipay` 1.0.46 (PyPI, 2026-02-18, MIT) is the **first release with subscription classes**
(`SubscriptionProduct`, `SubscriptionPricingPlan`, `SubscriptionCheckoutForm`, `Subscription`,
`SubscriptionCardUpdate`, `SubscriptionCustomer`; added by
[PR #105](https://github.com/iyzico/iyzipay-python/pull/105); v1.0.45 has none) (PY). It does not
fit the engine: synchronous `http.client`, `HTTPSConnection` without a timeout, headers kept in a
class-level dict mutated per request (unsafe under concurrency), raw response objects (PY source).
It adds calls missing from the docs (customer create/delete, card update `/with-subscription`) and
its search sample uses `parentReferenceCode` and `"YYYY-MM-DD hh:mm:ss"` dates where the docs say
`parent` and epoch ms. **Plan: call the REST API with `httpx` (async, explicit timeouts)** and use the
SDK only as a reference for signing.

## Unverified or contradictory

1. Webhook signature order: prose `secretKey, merchantId, …` vs code `merchantId + secretKey + …`.
2. Body to sign on body-less GETs (nothing per AUTH vs `null` in PY).
3. Product list paging in a GET body; search filters labelled path parameters; `parent` vs
   `parentReferenceCode`; date filters as epoch ms vs strings; plan `createdDate` string (PROD) vs
   epoch ms (PLAN); rate-limit paths `/api/v2/...` vs `/v2/...`.
4. Webhook delivery count (3 attempts vs 3 retries); format of the first-payment notification.
5. Cancel timing; downgrade semantics and proration; when `UNPAID` is set; automatic dunning.
6. Renewals and webhooks in sandbox; renewal time of day.
7. Add-on fee period; VAT; invoicing; refunds of subscription payments; Multicurrency for USD/EUR plans.
8. SDK-only endpoints (customer create/delete, card update `/with-subscription`).

## Consequences for the design

1. **Hosted form only.** Use the checkout form for start and card update; the direct NON3D call
   would put raw card data on our servers. Both are NON3D anyway, so the live account must have no
   3DS obligation: confirm when the merchant account is approved.
2. **Pull, don't trust pushes.** Treat callback and webhook as triggers; read state with the form
   result and subscription detail calls; verify `X-IYZ-SIGNATURE-V3` (constant-time) and dedupe on
   `iyziReferenceCode`. Few redeliveries → add a periodic reconciliation (search by status).
3. **Stable key = `parentReferenceCode`**, because an upgrade creates a new `referenceCode`.
4. **One plan per (tier, price, interval)**; monthly ↔ yearly is not an upgrade (cancel + new).
5. **No idempotency**: never blindly retry `initialize`, `upgrade` or `retry`; on a timeout query
   state first.
6. **Customers are keyed by email** across the whole merchant account: two organisations paid by
   the same person share one iyzico customer.
7. **Dunning, grace periods, tax and invoices are ours**; entitlement stays in our code
   (AGENTS rule 3). Name, e-mail, +90 phone, TCKN and address go to iyzico: billing metadata, a
   KVKK sub-processor disclosure, never prompt content.
