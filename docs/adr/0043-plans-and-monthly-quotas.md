# ADR-0043 — Plans and monthly quotas: limits in the signed tenant snapshot, counted from the usage ledger

- **Status:** Accepted (2026-10-30)
- **Date:** 2026-10-30
- **Deciders:** product owner (plan approved with decisions D1–D6, 2026-10-30); engineering
- **Builds on:** ADR-0019 (editions as entitlements, 413/429 semantics), ADR-0023 (per-tenant
  key-registry snapshots), ADR-0028 (metering), ADR-0033 (organizations carry an edition),
  ADR-0041 (the `usage_events` ledger)
- **Accepts in part:** ADR-0019 §3 (entitlements as signed data, here inside the tenant
  snapshot) and §4 (the request quota: 429 `quota_exceeded`, never a decision); ADR-0028 §2
  (test keys have their own small quota) and §3 for a single node (an in-process counter backed
  by the ledger). Their other parts stay Proposed; Redis stays the SaaS target of ADR-0028 §3.
- **Answers:** Q20 (over-quota behaviour, no grace margin); Q23 in part (single node)

## Context

The Community edition promises 10 000 requests a month (ADR-0018), but nothing enforces or
shows it: every limit is one deployment-wide setting (rate per key, prompt size), and tenants
carry an `edition` name with no values behind it. Since 2026-10-30 the usage ledger counts
every evaluation and every gateway call per tenant, environment and day (ADR-0041,
`usage` 0.3), so the count exists; what is missing is a limit per tenant, a way for the engine
and the gateway to know it, and a refusal at the limit.

Constraints: the engine reads no database on the request path for authorization (ADR-0023 §4:
snapshots, loaded like bundles); a quota is a billing control and must never become a decision
(ADR-0019 §4: not `BLOCK`, not `ALLOW`); edition values are data, not code (ADR-0019 §3); the
single-VM install (ADR-0032) has no Redis and runs the engine API and the gateway as separate
processes.

## Decision

1. **D1 — Entitlements travel in the tenant's signed key-registry snapshot** (credentials 0.7,
   snapshot payload 0.4): an `entitlements` block next to `keys`, verified, validated, swapped
   and hot-reloaded with them. Engine and gateway read a tenant's limits from the registry they
   already load. A tenant without a snapshot or without the block has no quota (metered only)
   and the deployment's size limit.
2. **D2 — The quota counts requests:** every admitted `POST /v1/evaluate` (ledger meter
   `evaluations` with source `api`) and every admitted AI Gateway call (meter `gateway_calls`),
   whatever the decision. A gateway call counts once although its prompt and its answer are
   both checked. Refused requests (401, 403, 413, 422, 429) do not count; failure-policy
   decisions do.
3. **D3 — Plans are a configuration file** (`UMAI_DE_PLANS_FILE`, contract `entitlements` 0.1)
   read by the policy service: limits per edition. The product's values ship as
   `deploy/plans.yaml`: Community 10 000 live requests and 1 000 test-key requests a month,
   32 000 characters per request; Enterprise metered, not refused. An operator can override
   single limits per tenant for a contract (`entitlement_overrides`, tenant-store migration
   0008) and change a tenant's edition (`PUT /v1/tenants/{id}/plan`). The policy service
   writes a tenant's effective entitlements into its snapshot whenever its keys, edition or
   overrides change, and for every tenant at startup (a changed plans file applies after a
   restart). Without a plans file no snapshot carries entitlements: no quotas (self-hosted
   default).
4. **D4 — Each process counts in memory, from the ledger.** Per tenant, month and environment
   (live or test) the count is the ledger's total at the last refresh plus the requests this
   process admitted since. Tenants with a finite limit that made a request are refreshed every
   `UMAI_DE_QUOTA_REFRESH_INTERVAL_S` (default 10 s) with one aggregate query each. No database
   call and no lock contention on the request path. The overshoot is bounded and documented:
   at most (processes × refresh interval × admitted request rate) plus the records not yet
   flushed; on the single VM (engine API and gateway) at 20 requests/s per key that is a few
   hundred requests above 10 000. The quota needs the records store (`UMAI_DE_RECORDS_SINK=database`);
   without it a process counts only what it admitted since it started (logged at startup).
5. **D5 — Over the limit: 429 `quota_exceeded`** before anything is evaluated or forwarded, with
   `limit`, `used`, `environment` and `resets_at` (the first instant of the next month, UTC) and
   `Retry-After` until then; the gateway answers in the OpenAI error shape with the same code.
   No grace margin; the console warns at 80 % and 100 % instead. If the ledger cannot be read
   the process keeps its last count and admits (ADR-0019 §4 default), logging the failure; a
   tenant whose count was never read is admitted and counted locally.
6. **D6 — Test keys** (`env: test`) have their own monthly limit (`monthly_test_requests`) and
   never count toward the live quota; everything else (live keys, the operator token, the
   console's playground) counts as live.
7. **Prompt size per tenant:** the snapshot's `max_input_chars` applies to `POST /v1/evaluate`
   and `/v1/detect`, capped by the deployment's `UMAI_DE_MAX_INPUT_CHARS`. The gateway keeps its
   own history limit (`UMAI_DE_GATEWAY_MAX_INPUT_CHARS`).
8. **Reading the plan:** `GET /v1/entitlements` in the policy service returns the tenant's
   edition, limits, this month's requests counted the same way (D2) and the reset time, for the
   console's usage page; same roles as `GET /v1/usage`.

## Alternatives considered

| Option | Why not |
|---|---|
| Separate entitlement snapshots (ADR-0019 §3 as first sketched) | A second loader, directory and signature path for the same tenant, with the same reload semantics; one file per tenant keeps keys and limits consistent |
| Count every check (`evaluations`) | A gateway call usually costs two, so gateway users would get half the requests the plan names |
| Plan values in code | A commercial change would need a release (ADR-0019) |
| A Postgres counter row incremented per request | A database write and a hot row on every request path; the ledger already holds the facts |
| Azure Managed Redis now (ADR-0028 §3) | A new dependency on the VM without a measurement showing it is needed (AGENTS rule 6); stays the target once SaaS runs many replicas, behind the same counter interface |
| A grace margin (e.g. +10 %) | Q20 asked; warnings at 80 % and 100 % give the same notice without a limit that is not the limit |
| Over quota → `BLOCK` or `ALLOW` | Ruled out by ADR-0019: an outage or a silent fail-open |

## Consequences

- Contracts: credentials 0.7 (snapshot payload 0.4 with `entitlements`), evaluation API 0.13
  (429 `quota_exceeded`; per-tenant `max_input_chars`), gateway API 0.13 (429
  `quota_exceeded`), new `entitlements` 0.1 (plans file, snapshot block, `GET /v1/entitlements`,
  `PUT /v1/tenants/{id}/plan`).
- Code: `tenancy/quota.py` (counter, refresh loop), the ledger count in `events/database.py`
  shared by the counter and the policy service, admission in the engine API and the gateway,
  `control_plane/plans.py`, tenant-store migration 0008.
- Operations: the VM ships `deploy/plans.yaml` and sets `UMAI_DE_PLANS_FILE`; smarttech (a
  Community organization) gets the Community limits.
- Console: the usage page shows the plan and this month's requests against the limit.
- Measured (2026-10-30, laptop): the admission check costs microseconds (see the PR); it adds
  no I/O to the request path.

## Validation

- Request `limit + 1` gets 429 `quota_exceeded` and nothing is evaluated (readiness M1);
  test keys are counted apart; refused requests are not counted; a new month starts at zero.
- The count follows the ledger: records written by another process are seen after one refresh.
- Ledger unreadable: requests are admitted and the failure is logged.
- `GET /v1/entitlements` equals the count the engine uses for the same ledger.
- Revisit when SaaS runs several engine replicas per region (Redis counters, ADR-0028 §3), or
  when the measured overshoot matters commercially.
