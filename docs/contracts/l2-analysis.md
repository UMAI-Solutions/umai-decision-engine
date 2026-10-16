# Contract: L2 analysis

**Version:** 0.1 (replaces the synchronous `l2-verdict` draft)

What the L2 model must return for one reviewed decision event, and the record the worker
stores. An analysis never changes the decision it reviews ([ADR-0010](../adr/0010-l2-asynchronous-review.md)).

## Model output (validated)

```json
{
  "category": "chain_exfiltration",
  "severity": "HIGH",
  "confidence": "medium",
  "policy_ids": ["pol-data-exfil"],
  "related_event_ids": ["ta-01J9X6…", "ta-01J9X7…"],
  "tags": ["chain.exfiltration"],
  "decision_assessment": "consistent",
  "rationale": "A tool call exported 40 customer records, a later prompt summarised them and this event sends the summary to an external assistant."
}
```

| Field | Type | Rules |
|---|---|---|
| `category` | `chain_exfiltration` \| `escalating_risk` \| `likely_false_positive` \| `policy_gap` \| `no_issue` | |
| `severity` | Severity | |
| `confidence` | `low` \| `medium` \| `high` | |
| `policy_ids` | string[] | Subset of the policies in the prompt |
| `related_event_ids` | string[] | Must exist in the provided history |
| `tags` | string[] | Only `chain.*` and `intent.*` codes from the taxonomy |
| `decision_assessment` | `consistent` \| `too_strict` \| `too_lenient` | L2's view of the L1 decision; informational for tuning |
| `rationale` | string | ≤ 500 chars after truncation; leak-checked; no placeholders expanded |

Any rule violation makes the output invalid (one retry, then the event is marked failed).

## Stored record (`L2Analysis`)

```json
{
  "contract_version": "0.1",
  "analysis_id": "an-01J9X9…",
  "event_id": "ta-01J9X7Z9AB",
  "tenant_id": "tenant-demo",
  "created_at": "2026-10-15T09:30:41Z",
  "model": "openai/gpt-oss-safeguard-20b",
  "prompt": "l2-review.v1",
  "reasoning_effort": "medium",
  "history_count": 7,
  "history_unavailable": false,
  "output": { "...": "model output above" },
  "finding_candidate": { "key": "ta-01J9X7Z9AB:chain_exfiltration", "created": true },
  "latency_ms": 6120.0,
  "attempt": 1
}
```

`finding_candidate.key` = `event_id:category` — the idempotency key that prevents duplicate
findings on redelivery. Who turns candidates into managed findings beyond R&D is open (Q13).

## Changelog

- 0.1 (2026-10-15): initial version for asynchronous review.
