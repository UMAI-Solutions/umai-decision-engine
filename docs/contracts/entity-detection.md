# Contract: entity detection result (L0 output)

**Version:** 0.2 · Internal (in-process) contract.

```json
{
  "entities": [
    {
      "type": "TR_TCKN",
      "start": 42,
      "end": 53,
      "score": 0.97,
      "recognizer": "tr_tckn",
      "validated": true,
      "value_hash": "b1946ac92492d2347c6235b4d2611184",
      "placeholder": "<TR_TCKN_1>"
    }
  ],
  "summary": [ { "type": "TR_TCKN", "count": 1, "distinct": 1 } ],
  "redacted_text": "… <TR_TCKN_1> …",
  "language": "tr",
  "truncated": false,
  "latency_ms": 9.8
}
```

| Field | Notes |
|---|---|
| `entities[].start`, `end` | Offsets into the **original** scanned text (after mapping back from normalization) |
| `entities[].score` | Presidio score after context enhancement, 0–1 |
| `entities[].validated` | `true` when a checksum validator passed; `null` when the type has no validator |
| `entities[].value_hash` | `HMAC-SHA256(tenant_key, canonical_value)`, first 16 bytes, hex |
| `entities[].placeholder` | Placeholder used in `redacted_text`; equal values share a placeholder |
| `summary[]` | Per type: `count` (occurrences) and `distinct` (distinct hashes) |
| `redacted_text` | Scanned text with every entity replaced. The only text that leaves L0 |
| `truncated` | Input exceeded `l0.max_chars`; scanned in chunks |

Offsets and hashes stay inside the engine (session store, L1 facts). The public response
exposes only `summary`.

## Changelog

- 0.2 (2026-10-15): breaking — removed `hard_block` and `legacy_preflight`; L0 returns no
  decisions (ADR-0011).
- 0.1 (2026-10-15): initial draft.
