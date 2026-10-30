# Contract: tagged action (decision event)

**Version:** 0.4

**Implementation status (2026-10-30):** emitted and stored by the engine API and the gateway
([ADR-0041](../adr/0041-records-decision-events-and-usage-ledger.md), write path
[ADR-0042](../adr/0042-records-write-path-in-memory-queue.md)), read through the policy service
(below), deleted after the retention period. Not yet delivered to L2 (`review_queued` stays
`false` until the review queue exists). Models: `contracts/events.py`.

One record per evaluation, built after the final decision is known and handed to the records
queue after the response is computed; it never delays or changes the decision. It is:

- the unit of dashboards, reports and investigations, and later of **session history**;
- the input to **asynchronous L2 review** when selected (`review: true`);
- the record audit and SIEM export are built from.

Also called the *decision event* (`karar olayı`). Stored in `decision_events`, append-only and
tenant-scoped (ADR-0025).

```json
{
  "contract_version": "0.4",
  "event_id": "ta-3f2b9c41d6e84a1b9c0d7e5f6a1b2c3d",
  "request_id": "req-01J9X7Z8QK",
  "tenant_id": "tenant-demo",
  "occurred_at": "2026-10-26T09:30:00Z",
  "source": "api",
  "engine_version": "0.1.0",
  "key_id": "AbCd1234",
  "environment": "live",
  "application_id": "main",
  "gateway_call_id": null,
  "bundle": {
    "id": "gr-demo-data-protection",
    "version": 3,
    "sha256": "9f2c1e0a6b7d4c3e8f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70",
    "mode": "ENFORCE"
  },
  "phase": "PRE_LLM",
  "subject": { "role": "sales", "department": "SALES" },
  "destination": { "app": "chatgpt.com", "class": "external_llm" },
  "agent_action": null,
  "entities": [ { "type": "TR_TCKN", "count": 2, "distinct": 2 } ],
  "tags": [
    { "code": "data.pii.tr_tckn", "layer": "L0", "score": 0.97 }
  ],
  "matched": [
    { "kind": "l0_policy", "id": "identity-data", "version": 2, "action": "BLOCK" }
  ],
  "decision": {
    "action": "BLOCK",
    "severity": "HIGH",
    "decided_by": "L0",
    "degraded": false,
    "floor": null,
    "would_have": null
  },
  "l1b": null,
  "review": false,
  "review_reasons": [],
  "errors": [],
  "latency_ms": { "total": 3.4, "l0": 3.1, "l1b": null }
}
```

| Field | Notes |
|---|---|
| `event_id` | The evaluation response's `event_id` (`ta-<uuid4 hex>`); idempotency key for storage, delivery and findings |
| `request_id` | The caller's correlation id, as sent |
| `tenant_id` | The tenant's external id (slug); the store keys rows by the tenant's uuid |
| `source` | `api` (`POST /v1/evaluate`) or `gateway`; other sources (`browser_extension`, `agent_sdk`, …) when they exist |
| `engine_version` | The engine release that decided |
| `key_id`, `environment`, `application_id` | The caller's API key, its `live`/`test` environment and its application (ADR-0039); `null` for the shared operator token |
| `gateway_call_id` | The gateway call the check belongs to (`gc-<uuid4 hex>`, also the call's usage record); `null` on the evaluation API. A gateway call has one event per check: input, each tool result, the answer. A stream's window re-checks are not recorded; only the stream's final check is, or the re-check that stopped it |
| `bundle` | Id, version, SHA-256 and mode of the guardrail version that decided (AGENTS.md rule 8); version and hash are `null` when the bundle could not be resolved |
| `subject` | Role and department only; user and session ids are not recorded |
| `destination` | As in the request |
| `agent_action` | Tool and server name of an agent action; never arguments or metadata |
| `entities[]` | Type, count and distinct count per entity type; no values, no value hashes |
| `tags[]` | Codes from [tag-taxonomy](tag-taxonomy.md) with the producing layer, score or probability |
| `matched[]` | The rules that produced the decision: kind, id, version, action |
| `decision` | The final runtime decision as returned; `floor` is the L0 gate's outcome when L1 ran on top of it (ADR-0036); `would_have` is the ENFORCE outcome in MONITOR mode |
| `l1b` | Absent when no L1b policy applied: provider, whether enforcing or shadow policies applied, the enforcing call's error kind and latency (shadow policies have their own call after the decision). Per-policy outcomes are L1b outcome records |
| `review`, `review_reasons` | Selected for L2 review, with the L1b reasons: `action` (the outcome is in the policy's `review_when.actions`), `low_confidence` (below `or_confidence_below`), `refusal` (the model refused) |
| `errors[]` | Layer, type and retryable; never the message |
| `latency_ms` | Total, L0 and L1b |

**Not recorded (ADR-0041 §2):** raw content, the redacted excerpt, per-value hashes, mask maps,
provider keys, error messages, `subject.user_id`, `subject.session_id`, agent action metadata.
The excerpt may return, encrypted per tenant and opt-in, once ADR-0025 §4 is built.

## L1b outcome record

One per L1b policy and evaluation, for enforcing and shadow policies alike. A shadow outcome
is written when its background call finishes, after the decision event (for a stream's re-check
that stopped the stream, also after the commit); records are never updated. Stored in
`l1b_outcomes`.

```json
{
  "contract_version": "0.4",
  "event_id": "ta-3f2b9c41d6e84a1b9c0d7e5f6a1b2c3d",
  "tenant_id": "tenant-demo",
  "occurred_at": "2026-10-26T09:30:00Z",
  "bundle_id": "gr-demo-data-protection",
  "bundle_version": 3,
  "phase": "PRE_LLM",
  "policy_id": "malicious-use",
  "policy_version": 1,
  "enforcement": "enforce",
  "provider": "openai",
  "outcome": "decided",
  "action": "BLOCK",
  "severity": "CRITICAL",
  "probability": 0.97,
  "rule": 0,
  "tags": ["misuse.cyber_weapons_mass_harm"],
  "review_reasons": ["action"],
  "error": null,
  "latency_ms": 395.4
}
```

| Field | Notes |
|---|---|
| `enforcement` | `enforce` (could decide) or `shadow` (recorded only) |
| `outcome` | `decided`, or `failed` when the provider call failed; then `action` is `null` and `error` holds the kind (`timeout`, `unavailable`, …) |
| `probability` | The deciding answer's probability when the rule has one |
| `rule` | Index of the matching `decide` rule; `null` when the policy's default applied |

## Read API (policy service)

Reads go through the policy service (`docs/architecture/policy-lifecycle.md`), which holds the
store and the roles (ADR-0029: owner, admin, policy editor, approver, viewer; the operator token
with `X-Umai-Tenant`). One tenant per request.

| Route | Returns |
|---|---|
| `GET /v1/decision-events` | Newest first. Query: `limit` (1–200, default 50), `cursor` (from the previous page), `since` / `until` (RFC 3339), `action`, `source`, `application_id`, `phase`, `review` (`true` / `false`) |
| `GET /v1/decision-events/{event_id}` | `{event, l1b_outcomes}`; 404 when the tenant has no such event (or it is past retention) |

```json
{
  "events": [
    {
      "contract_version": "0.4",
      "event_id": "ta-5d1c0b9a8e7f4a3b2c1d0e9f8a7b6c5d",
      "request_id": "gw-2c4e6a8b0d1f3a5c7e9b1d3f5a7c9e1b",
      "tenant_id": "tenant-demo",
      "occurred_at": "2026-10-30T08:15:00Z",
      "source": "gateway",
      "engine_version": "0.1.0",
      "key_id": "AbCd1234",
      "environment": "live",
      "application_id": "main",
      "gateway_call_id": "gc-8f7e6d5c4b3a29180716253443526170",
      "bundle": { "id": "gr-demo-data-protection", "version": 3, "mode": "ENFORCE" },
      "phase": "PRE_LLM",
      "decision": { "action": "ALLOW", "severity": "LOW", "decided_by": "L1b" },
      "latency_ms": { "total": 301.2, "l0": 1.1, "l1b": 299.8 }
    }
  ],
  "next_cursor": "MjAyNi0xMC0zMFQwODoxNTowMCswMDowMHw1ZDFj"
}
```

Records past their retention period are deleted by the policy service's retention job (ADR-0041
§5): decision events and L1b outcomes after `UMAI_DE_RECORDS_DECISION_RETENTION_DAYS` (default
30).

## Changelog

- 0.4 (2026-10-30): the gateway writes records (`source: gateway`, `gateway_call_id`, stream
  window re-checks not recorded); read API in the policy service; retention job.
- 0.3 (2026-10-26): breaking — emitted and stored (ADR-0041). Added `engine_version`,
  `key_id`, `environment`, `application_id`, `gateway_call_id`, `bundle.sha256`, `bundle.mode`,
  `matched`, `decision.would_have`, `errors`, `latency_ms`; `source` is `api` or `gateway`;
  `action` renamed `agent_action` (tool and server name only); `l1b` reduced to a summary, with
  per-policy outcomes as L1b outcome records. Removed from the record: `redacted_excerpt`,
  `entities[].hashes`, `history_key`, `subject.agent_id`, `evidence_ref` (not stored, ADR-0041
  §2).
- 0.2 (2026-10-15): breaking — `action_id` → `event_id`; `l1`/`final` merged into
  `decision` (no `l2_override`, ADR-0010); `escalate` → `review`; added `source`, `l1b`,
  `evidence_ref`.
- 0.1 (2026-10-15): initial draft.
