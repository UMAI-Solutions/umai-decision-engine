# Contract: usage record (ledger)

**Version:** 0.3

**Implementation status (2026-10-30):** written by the engine API and the gateway
([ADR-0041](../adr/0041-records-decision-events-and-usage-ledger.md), ledger shape from
[ADR-0028](../adr/0028-metering-entitlements-and-billing.md) §1–§2), read with `GET /v1/usage` in
the policy service, deleted after 400 days. Models: `contracts/events.py`.

The ledger is UMAI's own record of what a tenant used: the source for usage pages, quotas
(ADR-0019 §4), exports and invoices (ADR-0028). One record per meter and event, append-only and
tenant-scoped (`usage_events`, ADR-0025); `(tenant_id, event_id, meter)` is unique, so a
retried write never counts twice.

```json
{
  "contract_version": "0.3",
  "event_id": "ta-3f2b9c41d6e84a1b9c0d7e5f6a1b2c3d",
  "tenant_id": "tenant-demo",
  "occurred_at": "2026-10-26T09:30:00Z",
  "meter": "evaluations",
  "quantity": 1,
  "source": "api",
  "key_id": "AbCd1234",
  "environment": "live",
  "application_id": "main",
  "bundle_id": "gr-demo-data-protection",
  "bundle_version": 3,
  "decision": "BLOCK",
  "degraded": false
}
```

| Meter | Quantity | Written |
|---|---|---|
| `evaluations` | 1 per admitted `POST /v1/evaluate`, failure-policy decisions included; requests refused before evaluation (401, 403, 413, 422, 429) are not counted | with the decision event |
| `l1b_calls` | Decision-model calls made for the evaluation, shadow calls included: with `batch: all` one for the enforcing policies and one for the shadow policies (ADR-0040 amendment 2026-10-30), with `per_policy` one per policy; plus fallback calls | when the evaluation's last L1b call finishes (the shadow call, after the decision, when there is one) |
| `l1b_input_tokens`, `l1b_output_tokens` | Tokens the provider reported for those calls | with `l1b_calls`, if the provider reports them |
| `gateway_calls` | 1 per gateway call that passed admission (Chat Completions, Responses, embeddings), whatever its outcome: answered, refused, upstream error, timeout. Each check of the call is also an `evaluations` record (source `gateway`) | when the call ends (a stream: when it closes) |
| `provider_input_tokens`, `provider_output_tokens` | Tokens the upstream provider reported for the call (`prompt_tokens` / `input_tokens`, `completion_tokens` / `output_tokens`) | with `gateway_calls`, when reported |

| Field | Notes |
|---|---|
| `event_id` | The evaluation's event id; for gateway meters the call's id (`gc-<uuid4 hex>`) |
| `environment` | `live` or `test` (ADR-0023 §3); test keys are metered separately and never billed; `null` for the shared operator token |
| `decision`, `degraded` | The final decision of the evaluation (`evaluations` only) |
| `provider`, `model` | Gateway meters: the provider and upstream model the call went to (0.2) |

**Retention:** 400 days (ADR-0041 §5, `UMAI_DE_RECORDS_USAGE_RETENTION_DAYS`). No content, ever.

**Export:** a month of the ledger per UTC day and dimension as CSV, `GET /v1/usage/export.csv`, and
the whole retained ledger in the organization archive: [data-export](data-export.md) §2–§3.

## Read API (policy service)

`GET /v1/usage?month=YYYY-MM` (default: the current month, UTC) returns the month's ledger for
the tenant: totals per meter, the same split by source, environment and application, and per UTC
day (`daily`, days with usage only, oldest first; 0.3). Roles
(ADR-0029): owner, admin, policy editor, approver, viewer, billing; the operator token with
`X-Umai-Tenant`.

```json
{
  "contract_version": "0.3",
  "tenant_id": "tenant-demo",
  "month": "2026-10",
  "from": "2026-10-01T00:00:00Z",
  "to": "2026-11-01T00:00:00Z",
  "totals": { "evaluations": 1250, "gateway_calls": 410, "provider_input_tokens": 182340 },
  "groups": [
    { "meter": "evaluations", "source": "api", "environment": "live", "application_id": "main", "quantity": 430 },
    { "meter": "evaluations", "source": "gateway", "environment": "live", "application_id": "main", "quantity": 820 },
    { "meter": "gateway_calls", "source": "gateway", "environment": "live", "application_id": "main", "quantity": 410 },
    { "meter": "provider_input_tokens", "source": "gateway", "environment": "live", "application_id": "main", "quantity": 182340 }
  ],
  "daily": [
    { "date": "2026-10-29", "meters": { "evaluations": 610, "gateway_calls": 200, "provider_input_tokens": 90120 } },
    { "date": "2026-10-30", "meters": { "evaluations": 640, "gateway_calls": 210, "provider_input_tokens": 92220 } }
  ]
}
```

## Changelog

- 0.3 (2026-10-30): `daily` in `GET /v1/usage` (per UTC day and meter).
- 0.2 (2026-10-30): gateway meters `gateway_calls`, `provider_input_tokens`,
  `provider_output_tokens` with `provider` and `model`; read API `GET /v1/usage`; retention job.
- 0.1 (2026-10-26): initial version, written by the engine API (ADR-0041).
