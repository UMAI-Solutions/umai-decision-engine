# Contract: entitlements (plans, tenant limits, quota read)

**Version:** 0.1 · **Producer → consumer:** deployment configuration → policy service (plans
file), policy service → engines (the `entitlements` block of the key-registry snapshot,
[credentials](credentials.md) 0.7), policy service ↔ console (`GET /v1/entitlements`), operator
→ policy service (`PUT /v1/tenants/{id}/plan`) · **Decisions:**
[ADR-0043](../adr/0043-plans-and-monthly-quotas.md), [ADR-0019](../adr/0019-editions-as-runtime-entitlements.md)

A plan names limits; a tenant's **edition** picks its plan; an operator may override single
limits per tenant. The policy service writes the tenant's effective limits into its signed
snapshot, and the engine API and the gateway enforce them at admission. No limit value lives
in code.

## Limits — `Limits`

| Field | Type | Meaning |
|---|---|---|
| `monthly_requests` | integer ≥ 0 or null | Requests per calendar month (UTC) with live keys, the operator token and the console. A request is an admitted `POST /v1/evaluate` or an admitted AI Gateway call (a gateway call counts once, ADR-0043 D2). Over it: 429 `quota_exceeded`. Null: counted, never refused |
| `monthly_test_requests` | integer ≥ 0 or null | The same for keys with `env: test`, counted apart (D6) |
| `max_input_chars` | integer ≥ 1000 or null | Characters per `POST /v1/evaluate` (evaluation API 413 `input_too_large`), capped by the deployment's `UMAI_DE_MAX_INPUT_CHARS`. Null: the deployment's limit |

## Plans file

YAML, path `UMAI_DE_PLANS_FILE`, read by the policy service at startup. One entry per edition
(`community`, `enterprise`); an edition the file does not name has no limits. Without the file,
no snapshot carries entitlements and nothing is refused for quota (self-hosted default).

```yaml
contract_version: "0.1"
plans:
  community:
    monthly_requests: 10000
    monthly_test_requests: 1000
    max_input_chars: 32000
  enterprise:
    monthly_requests: null
    monthly_test_requests: null
    max_input_chars: null
```

## Snapshot block — `TenantEntitlements`

Inside the tenant's key-registry snapshot (credentials 0.7, payload `contract_version` 0.4),
signed with it. The limits as above (null fields are omitted, which means the same), plus the
edition they came from:

```json
{
  "edition": "community",
  "monthly_requests": 10000,
  "monthly_test_requests": 1000,
  "max_input_chars": 32000
}
```

A snapshot without the block (or a tenant without a snapshot) has no quota and the
deployment's size limit.

## `GET /v1/entitlements` (policy service)

The calling tenant's plan and this month's count, for the console's usage page. Same roles as
`GET /v1/usage` (records.usage.read). `used` counts the ledger the way the engine does (D2, D6);
the engine may be a few seconds ahead (ADR-0043 D4).

```json
{
  "contract_version": "0.1",
  "tenant_id": "smarttech",
  "edition": "community",
  "limits": {
    "monthly_requests": 10000,
    "monthly_test_requests": 1000,
    "max_input_chars": 32000
  },
  "month": "2026-10",
  "used": { "requests": 412, "test_requests": 3 },
  "resets_at": "2026-11-01T00:00:00Z"
}
```

## `PUT /v1/tenants/{tenant_id}/plan` (operator)

Sets the tenant's edition and replaces its overrides; republishes the tenant's snapshot. The
operator token only (no person role holds it). In `overrides` a field that is present replaces
the plan's value (null: no limit); an absent field keeps the plan's.

```json
{
  "edition": "enterprise",
  "overrides": { "monthly_requests": 50000 }
}
```

Answer: the tenant record, its overrides, its effective limits and whether the snapshot was
published.

## Changelog

- 0.1 (2026-10-30, ADR-0043): initial: limits, plans file, snapshot block, `GET /v1/entitlements`,
  `PUT /v1/tenants/{id}/plan`.
