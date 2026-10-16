# Contract: tagged action (decision event)

**Version:** 0.2

One record per evaluation, built by L1 after the final decision is known and emitted after
the response is computed. It is:

- the unit of **session history**;
- the input to **asynchronous L2 review** when selected (`review: true`);
- the record evals and future audit/SIEM export are built from.

Also called the *decision event* (`karar olayı`).

```json
{
  "contract_version": "0.2",
  "event_id": "ta-01J9X7Z9AB",
  "request_id": "req-01J9X7Z8QK",
  "tenant_id": "tenant-demo",
  "source": "browser_extension",
  "occurred_at": "2026-10-15T09:30:00Z",
  "bundle": { "id": "gr-demo-data-protection", "version": 3 },
  "history_key": { "kind": "session", "value": "s-456" },
  "subject": { "role": "sales", "department": "SALES", "agent_id": null },
  "phase": "PRE_LLM",
  "destination": { "app": "chatgpt.com", "class": "external_llm" },
  "action": null,
  "entities": [ { "type": "TR_TCKN", "count": 7, "distinct": 7, "hashes": ["b1946a…", "…"] } ],
  "redacted_excerpt": "Bu listeyi özetle: <TR_TCKN_1> … <TR_TCKN_7>",
  "tags": [
    { "code": "data.pii.tr_tckn", "layer": "L0", "score": 0.97 },
    { "code": "data.pii.bulk", "layer": "L1a", "rule_id": "r-bulk-tckn-external" },
    { "code": "intent.data_exfiltration", "layer": "L1b", "probability": 0.81,
      "policy_id": "pol-data-exfil", "policy_version": 3 }
  ],
  "l1b": {
    "provider": "onprem_decider",
    "model": "<pinned model id>",
    "answers": { "pol-data-exfil.intent": { "value": "data_exfiltration", "confidence": 0.81 } },
    "min_confidence": 0.81,
    "probabilities_available": true,
    "refused": false
  },
  "decision": {
    "action": "BLOCK",
    "severity": "HIGH",
    "decided_by": "L1b",
    "floor": "ALLOW",
    "degraded": false
  },
  "review": true,
  "review_reasons": ["action_in_review_list"],
  "evidence_ref": null
}
```

| Field | Notes |
|---|---|
| `event_id` | Unique; idempotency key for delivery and findings |
| `source` | `browser_extension`, `agent_sdk`, `copilot_studio`, `eval`, … (from settings/caller) |
| `history_key` | `session` → `agent` → `user`, first available; never sent to providers |
| `subject` | No `user_id` in the record sent to L2; the store keeps it in the key only |
| `entities[].hashes` | Distinct value hashes, max 50 per type; lets L2 see repeated values across events |
| `redacted_excerpt` | ≤ 1 000 chars around entities and the end of the focus message |
| `tags[]` | Codes from [tag-taxonomy](tag-taxonomy.md); each records the producing layer |
| `l1b` | Absent when L1b did not run; `answers` carries values and confidences only |
| `decision` | The final runtime decision, as returned to the caller |
| `review_reasons` | `action_in_review_list`, `low_confidence`, `force_review_rule`, `refusal`, `bundle_review_rule` |
| `evidence_ref` | Reserved for a pointer to stored evidence (beyond R&D) |

What L2 receives is this record minus `tenant_id`, `request_id`, `history_key` and hashes
(hashes are replaced by "value seen in N other events" counts).

## Changelog

- 0.2 (2026-10-15): breaking — `action_id` → `event_id`; `l1`/`final` merged into
  `decision` (no `l2_override`, ADR-0010); `escalate` → `review`; added `source`, `l1b`,
  `evidence_ref`.
- 0.1 (2026-10-15): initial draft.
