# ADR-0041 — Records v1: engine and gateway write decision events, L1b outcomes and usage records to the regional tenant store

- **Status:** Accepted (2026-10-26)
- **Date:** 2026-10-26
- **Deciders:** product owner (WP6 plan approved with decisions D1–D5, 2026-10-26); engineering
- **Builds on:** ADR-0010 (decision events, L2 delivery), ADR-0025 (RLS rules for tenant
  tables), ADR-0035 (no content beyond L0 and the gateway zone), ADR-0039 (records carry
  application and key), ADR-0040 (L1b outcomes, shadow)
- **Accepts in part:** ADR-0030 §1 (decision events), §3 (append-only tables, INSERT/SELECT for
  the application role) and §4 (decision-event retention); ADR-0028 §1–§2 (the `usage_events`
  ledger and its meters). Their other parts stay Proposed.
- **Answers:** Q13 in part (records store and retention; L2 delivery and findings stay open),
  Q31 for decision events and usage
- **Write path:** ADR-0042

## Context

Nothing records what UMAI decided. The evaluation API returns an `event_id` that points
nowhere, `review_queued` is always `false`, L1b shadow outcomes exist only as log lines, and
usage is not counted. Dashboards, quotas, invoices, the audit trail, L2 review and SIEM export
all need the same facts, stored per tenant (`docs/security/gap-analysis.md` D4,
`docs/security/launch-plan.md` S1-1, S1-4).

ADR-0030 and ADR-0028 proposed the shapes on 2026-10-17 but were never accepted, and the
`tagged-action` contract (0.2) is defined but never emitted. It also lacks fields that other
decisions require: the bundle hash (AGENTS.md rule 8), the engine version (ADR-0019), the
application and key (ADR-0039). Only the policy service connects to the tenant store today;
`db/__init__.py` forbids the request path from importing it.

## Decision

1. **Three append-only, tenant-scoped tables** (migration 0006, ADR-0025 rules: `tenant_id`
   uuid, ENABLE + FORCE RLS, the `tenant_isolation` policy):
   - `decision_events` — one row per evaluation, contract `tagged-action` 0.3;
   - `l1b_outcomes` — one row per L1b policy and evaluation, enforcing and shadow, written when
     the outcome is known (a shadow outcome arrives after the decision; rows are never updated);
   - `usage_events` — the ledger, contract `usage` 0.1; meters `evaluations` and `l1b_calls`,
     `l1b_input_tokens`, `l1b_output_tokens` from the engine API; the gateway's meters land with
     the gateway records.
   Uniqueness on `(tenant_id, event_id)`, `(tenant_id, event_id, policy_id)` and
   `(tenant_id, event_id, meter)` makes every write idempotent.
2. **No content, not even redacted (D2).** A record holds ids, types, counts, codes, actions,
   probabilities and latencies. The contract's `redacted_excerpt` and per-value hashes are **not
   stored** in v1: an excerpt is still the caller's prose and value hashes link one person's
   values across events (KVKK). Error messages are not stored (they can carry exception text);
   error types are. Subject: role and department only; agent action: tool and server names only.
   Request ids are stored (callers' correlation ids, already in logs).
3. **Who writes.** The engine API and the gateway write with the **application role** (INSERT
   and SELECT only on the three tables, ADR-0030 §3); the policy service reads. This amends the
   rule in `db/__init__.py`: the request path still never *waits* for the database, because the
   pipeline only hands records to an in-process queue (ADR-0042); the writer task connects.
   The external `tenant_id` (the slug) is mapped to the tenant's uuid once per process.
4. **Granularity (D5).** The engine API writes one decision event and one `evaluations` usage
   record per admitted evaluation, including failure-policy decisions (ADR-0028 §2). The gateway
   (next change) writes one usage record per call and one decision event per check, except that
   the window re-checks of one stream collapse into the stream's final check.
5. **Retention (D3).** Decision events and L1b outcomes: 30 days by default, per tenant up to
   400 days where the edition allows (ADR-0030 §4); usage: 400 days (invoices and disputes). A
   retention job in the policy service deletes older rows with the owner role (next change).
6. **Event ids** keep the engine's format, `ta-<uuid4 hex>` (not ULID as ADR-0030 drafted):
   ordering comes from `occurred_at`, which is indexed with the tenant.
7. **Reads go through the policy service** (`GET /v1/decision-events`, `GET /v1/usage`, next
   change), which already has the store and the roles (ADR-0029: `viewer` reads decisions and
   usage, `billing` reads usage).

## Alternatives considered

| Option | Why not |
|---|---|
| Store the redacted excerpt and value hashes now (contract 0.2 as written) | Content and linkable data under KVKK before retention, erasure and per-tenant encryption (ADR-0025 §4) exist; can return as an Enterprise option |
| One decision event per gateway call instead of per check | A call has several checks with different outcomes (input, tool output, answer); the per-check record is what L2 and investigations need. Stream window re-checks are the exception (D5) |
| A separate events database or a log pipeline (Log Analytics, Event Hubs) as the store | Second store for the same tenant data, RLS and erasure twice; the export targets of ADR-0030 §5 read from these tables later |
| The policy service as the only writer (engine posts records to it) | A hop and a second queue on the hot process; the engine already holds everything the record needs |
| ULID event ids | Changes a field callers already see; sortability is covered by `occurred_at` |

## Consequences

- `events/` exists: a `Recorder` the pipeline calls at every exit (no-op by default, so golden
  tests, evals and the policy service record nothing), records built from the response, and the
  writer of ADR-0042.
- Engine and gateway need `UMAI_DE_DATABASE_URL` (application role) and
  `UMAI_DE_RECORDS_SINK=database`; on the single VM they start after the policy service, which
  runs the migrations.
- The leak suite also scans stored records.
- Must still be built: gateway records, read routes, the retention job, console pages (WP7),
  quota enforcement from the ledger (ADR-0019 §4, ADR-0028 §3), L2 delivery of `review` events.

## Implementation

- 2026-10-26 (PR 1): engine API records, migration 0006, write path (ADR-0042).
- 2026-10-30 (PR 2): gateway records. Each call gets a server-made id (`gc-<uuid4 hex>`; a
  caller's request id may repeat) that names its usage record (`gateway_calls`,
  `provider_input_tokens`, `provider_output_tokens`, with provider and model, migration 0007)
  and the `gateway_call_id` of every check's decision event. The usage record is written once per
  call: by the handler, by the stream when it closes, or by an app middleware for exits raised as
  errors (refusals with `refusal: error`, upstream failures, timeouts). A stream's window
  re-checks run with records held on the evaluation and dropped, except the re-check that stops
  the stream, which is committed; the final check is recorded. Read API in the policy service
  (`GET /v1/decision-events`, `/v1/decision-events/{id}`, `/v1/usage`; permissions
  `records.decisions.read`, `records.usage.read`). Retention job in the policy service, hourly,
  with the owner role, tenant by tenant (RLS stays forced): 30 days of decision events and L1b
  outcomes, 400 days of usage (`UMAI_DE_RECORDS_*_RETENTION_DAYS`); per-tenant ranges come with
  entitlements. Retention is not yet an audit event (ADR-0030 §3 waits for audit events).

## Validation

- Isolation tests I1–I3 cover the three tables; a Postgres test shows the application role
  cannot UPDATE or DELETE them.
- The leak suite finds no planted value in stored records.
- Evaluation latency with the database recorder stays within noise of the no-op recorder
  (measured, ADR-0042).
- Revisit when per-tenant encryption lands (ADR-0025 §4): then the excerpt can be stored
  encrypted for tenants that opt in.
