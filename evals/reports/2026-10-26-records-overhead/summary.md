# records overhead

- **Date:** 2026-10-26
- **Store:** PostgreSQL (local Docker postgres:17-alpine)
- **Evaluations:** 600 per run, synthetic `l0-pii-tr` dev texts, guardrail `gr-demo-data-protection` (active version), PRE_LLM, 4 L0 threads
- **Compared:** the no-op recorder against the database recorder (queue of ADR-0042, batched writer); latency of `Evaluator.evaluate`, so the write is off the measured path by design and any difference is the cost of building and queueing records

| Recorder | Concurrency | p50 ms | p95 ms | p99 ms | Evaluations/s | Records written (dropped) |
|---|---|---|---|---|---|---|
| none | 1 | 1.98 | 3.47 | 7.99 | 438 | — |
| memory | 1 | 2.28 | 3.84 | 8.27 | 360 | — |
| database | 1 | 2.74 | 5.63 | 12.29 | 299 | 1300 (0) |
| none | 16 | 61.78 | 82.02 | 101.53 | 248 | — |
| memory | 16 | 65.67 | 88.48 | 121.95 | 235 | — |
| database | 16 | 65.92 | 94.40 | 113.13 | 234 | 1300 (0) |
