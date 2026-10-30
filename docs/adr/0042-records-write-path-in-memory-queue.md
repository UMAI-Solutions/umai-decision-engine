# ADR-0042 — Records write path: a bounded in-memory queue, batched inserts off the request path, drop and count when full

- **Status:** Accepted (2026-10-26)
- **Date:** 2026-10-26
- **Deciders:** product owner (WP6 decision D4, 2026-10-26); engineering
- **Builds on:** ADR-0041 (what is recorded), `docs/architecture/decision-merge-and-failure.md`
  (rule 6: the event is emitted after the response and never changes or delays it; "event emit
  failure": the decision is unaffected, the emit is retried and counted), AGENTS.md rule 4
- **Amends:** ADR-0030 §3 for the engine and gateway: their records do not go through a durable
  outbox yet

## Context

A record must never slow or change a decision: the evaluation budget is 1 500 ms by default
and L1b already uses most of it (ADR-0040). ADR-0030 §3 drafted a durable outbox written in the
same transaction as the change. That fits the policy service, whose changes are database
transactions; the engine and gateway have no transaction to join, run with read-only file
systems on the single VM (`deploy/vm/docker-compose.yml`), and have nowhere durable to keep a
local outbox without adding a volume.

## Decision

1. **The pipeline hands records to a bounded `asyncio.Queue`** (`put_nowait`, never awaited on
   the request path). Capacity `UMAI_DE_RECORDS_QUEUE_SIZE` (default 10 000 records).
2. **One writer task per process** takes up to `UMAI_DE_RECORDS_BATCH_SIZE` records (default
   200) or what arrived within `UMAI_DE_RECORDS_FLUSH_INTERVAL_MS` (default 500 ms), groups them
   by tenant and inserts each group in one `tenant_session` transaction, with
   `UMAI_DE_RECORDS_WRITE_TIMEOUT_MS` (default 5 000 ms) per batch. Each table has one insert
   statement, compiled once and executed for all rows of a batch (executemany); a fresh
   multi-row statement per batch cost tens of milliseconds of event-loop time (measured below).
   Inserts ignore rows that already exist (the uniqueness of ADR-0041 §1), so a retried batch
   never duplicates.
3. **When the store fails**, the writer keeps the batch and retries with backoff (0.5 s doubling
   to 30 s) while new records keep queueing. **When the queue is full**, a new record is
   dropped: the decision is unaffected, the drop is counted by reason (`queue_full`,
   `unknown_tenant`, `shutdown`) and logged at most once a minute. This is the explicit,
   recorded behaviour rule 4 asks for; it is a loss of records, never of decisions.
4. **Shutdown** stops taking traffic, drains L1b's background calls (their outcomes are
   records), then flushes the queue within `UMAI_DE_RECORDS_DRAIN_TIMEOUT_MS` (default
   5 000 ms); what remains is dropped and counted.
5. **Observability without a metrics stack:** every `UMAI_DE_RECORDS_STATS_INTERVAL_S`
   (default 60 s) the writer logs `records.stats` with written, dropped by reason, failed batches
   and backlog. Ids and counts only.
6. **Off by default.** `UMAI_DE_RECORDS_SINK=none` records nothing; `database` requires
   `UMAI_DE_DATABASE_URL` and refuses to start without it.

## Alternatives considered

| Option | Why not |
|---|---|
| Insert synchronously on the request path | Adds a database round trip (and its failure modes) to every decision; violates rule 6 of the failure doc |
| Durable local outbox (SQLite file or journal on a volume), as ADR-0030 §3 drafted | Survives a crash, but needs a writable volume per container and a second writer to the same rows; worth it once records carry billing weight, revisit with quotas |
| A queue service (Redis streams, Event Hubs, Service Bus) | New infrastructure on the single VM; ADR-0028 brings Redis later and the writer can move behind it |
| Block when the queue is full (backpressure into the pipeline) | Turns a records outage into a decision outage |

## Consequences

- A crash or a long database outage loses at most the queued records (by default 10 000); the
  stats line and the drop log make it visible. Usage drift from lost records is what the
  reconciliation of ADR-0028 §6 must account for once billing starts.
- The leak suite and tests can inject an in-memory recorder; the writer is tested against
  SQLite and, in CI, Postgres.
- Revisit with billing (ADR-0028): invoices may need the durable outbox.

## Validation

- Tests: batching, idempotent retry, queue-full drops counted, shutdown flush, writer failure
  and recovery (`tests/unit/test_records_queue.py`); the writer on SQLite and, with the
  application role, on PostgreSQL (`tests/integration/test_records_store.py`,
  `tests/db/test_isolation.py`).
- Benchmark (`dev/bench_records.py`, [report](../../evals/reports/2026-10-26-records-overhead/summary.md);
  600 evaluations of synthetic texts, demo guardrail, local PostgreSQL 17, Windows laptop):

  | Recorder | Concurrency | p50 ms | p95 ms | p99 ms | Evaluations/s |
  |---|---|---|---|---|---|
  | none | 1 | 1.98 | 3.47 | 7.99 | 438 |
  | records built, not written | 1 | 2.28 | 3.84 | 8.27 | 360 |
  | database | 1 | 2.74 | 5.63 | 12.29 | 299 |
  | none | 16 | 61.78 | 82.02 | 101.53 | 248 |
  | records built, not written | 16 | 65.67 | 88.48 | 121.95 | 235 |
  | database | 16 | 65.92 | 94.40 | 113.13 | 234 |

  The write is off the request path, but the writer shares the process: about +0.8 ms at the
  median and +2 ms at p95 per evaluation, against an L1b call of 280–570 ms (ADR-0040) and a
  1 500 ms budget. With a fresh multi-row statement per batch it was +24 ms (sequential p99) and
  p95 261 ms under concurrency 16; hence the compiled statement. No record was dropped.
- Revisit if a production profile shows the writer competing with L0 for the event loop: the
  writer can move to its own thread and loop without changing the recorder's interface.
