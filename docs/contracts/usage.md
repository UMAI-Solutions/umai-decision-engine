# Contract: usage record (ledger)

**Version:** 0.1

**Implementation status (2026-10-26):** written by the engine API for every admitted
evaluation ([ADR-0041](../adr/0041-records-decision-events-and-usage-ledger.md), ledger shape
from [ADR-0028](../adr/0028-metering-entitlements-and-billing.md) §1–§2); the gateway's meters
and the read API (`GET /v1/usage` in the policy service) follow. Model: `contracts/events.py`.

The ledger is UMAI's own record of what a tenant used: the source for usage pages, quotas
(ADR-0019 §4), exports and invoices (ADR-0028). One record per meter and event, append-only and
tenant-scoped (`usage_events`, ADR-0025); `(tenant_id, event_id, meter)` is unique, so a
retried write never counts twice.

```json
{
  "contract_version": "0.1",
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
| `l1b_calls` | Decision-model calls made for the evaluation (1 with `batch: all`, one per policy with `per_policy`, plus a fallback call), shadow calls included | when the L1b call finishes |
| `l1b_input_tokens`, `l1b_output_tokens` | Tokens the provider reported for those calls | when the L1b call finishes, if the provider reports them |

| Field | Notes |
|---|---|
| `event_id` | The evaluation's event id |
| `environment` | `live` or `test` (ADR-0023 §3); test keys are metered separately and never billed; `null` for the shared operator token |
| `decision`, `degraded` | The final decision of the evaluation (`evaluations` only) |

**Retention:** 400 days (ADR-0041 §5). No content, ever.

## Changelog

- 0.1 (2026-10-26): initial version, written by the engine API (ADR-0041).
