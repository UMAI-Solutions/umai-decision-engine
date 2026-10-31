# ADR-0045 — Audit log: one event per policy-service change and per refused attempt, append-only per tenant, exportable as CSV

- **Status:** Accepted (2026-10-31); it accepts ADR-0030 §1 for audit events, §2 as narrowed
  below, §4 for audit retention and §5's audit-log page and API
- **Date:** 2026-10-30
- **Deciders:** engineering proposal; product owner (retention default, what the console shows)
- **Builds on:** ADR-0005 (no content beyond L0), ADR-0023 §1 (on-behalf-of assertions name the
  person), ADR-0025 (tenant store, RLS), ADR-0029 (permission matrix, deny by default), ADR-0030
  (audit events), ADR-0041 (records: append-only tables, retention job), ADR-0044 (billing)
- **Answers:** launch plan S0-7 ("audit log visible in console"); the Cloud SaaS checklist item
  "audit log: who, when, what changed; exportable"

## Context

Every configuration change reaches the policy service, and since ADR-0029 every route there
knows its principal (a person from the assertion, or the operator) and its permission. Only
publish and activate leave a trace with an actor (the bundle change log, readiness A1); API keys,
members, invitations, applications, providers, gateway settings, plans and billing changes leave
an INFO log line at best, and refused attempts leave a WARNING. A customer's admin cannot answer
"who gave Grace admin rights" or "who revoked the CI key", and an auditor cannot be handed a
file. ADR-0030 proposed the full design (outbox, hash chain, SIEM export); the product needs the
first useful slice now, without the parts that wait for infrastructure.

## Decision

1. **What is recorded.** One audit event (contract [audit](../contracts/audit.md) 0.1) per call
   of a changing policy-service route (`POST`, `PUT`, `PATCH`, `DELETE` under `/v1`): `success`
   when it answered 2xx, `denied` when it answered 403 (missing role, wrong tenant, person
   required, owner-only change). Other failures (404, 409, 422) change nothing and are not
   recorded. Reading is not recorded, except the CSV export (`audit.export`).
2. **Actions** are the permission names of ADR-0029 where one route is one change (`key.create`,
   `member.update`, `guardrail.activate`) and a verb under the permission where a permission
   covers several routes (`invitation.create`, `invitation.revoke` for `invitation.manage`;
   `billing.cancel` for `billing.manage`). One target type per action, so a refused attempt names
   it too.
3. **Shape, narrowed from ADR-0030 §2.** Actor = type (`person`, `operator`), the subject from
   the assertion and the person's user id in the store. **No e-mail, IP or user agent is stored**:
   the table is append-only for 400 days, and an erasure request must not need an `UPDATE`; the
   read API resolves e-mail and name from the users table when read, and shows none once the
   person is erased. `details` holds ids and the before/after of non-secret fields (roles,
   names, editions, versions, flags), at most 4 KB; never content, secrets, key material,
   tokens, card data or billing details. A publish or activation note is kept (ADR-0030's
   `reason`). The hash chain and `api_key` / `service` / `system` actors are left for later.
4. **Where.** The tenant table `audit_events` (migration 0010): RLS like every tenant table,
   append-only for the application role (INSERT, SELECT; `rls.APPEND_ONLY_TABLES`). A person's
   refused attempt is recorded in the tenant their assertion names, never written into another
   tenant's log; the operator's in the tenant the request targets.
5. **How.** The `/v1` router's route class (`AuditRoute`) writes the event after the route has
   answered; each changing route declares its action with a dependency and names its target and
   details. The write is awaited in the request, in its own transaction, **after** the change:
   most changes are files, signed bundles or calls to the payment provider, outside the tenant
   store, so ADR-0030 §3's outbox cannot share their transaction. A failed write is logged at
   ERROR with the event id, tenant and action (AGENTS rule 4), and the change stands, as it
   already happened. A route-walk test fails for a changing route without an action (as the
   permission test does, ADR-0029 §6), and a second walk calls each one without the right and
   expects its `denied` event.
6. **Access.** `GET /v1/audit-events` (filters: time, actor, action or action prefix, target,
   outcome; cursor paging as `/v1/decision-events`) and `GET /v1/audit-events/export` (CSV,
   newest first, at most 50 000 rows per file, formula cells quoted), both behind the new
   permission `audit.read` for owners, admins and the operator. The console shows them under
   Organization → Audit log.
7. **Retention.** 400 days for every edition (ADR-0030 §4), applied by the existing retention
   job with the owner role (`UMAI_DE_AUDIT_RETENTION_DAYS`, 30–3650).

## Alternatives considered

| Option | Why not |
|---|---|
| ADR-0030 §3's outbox in the same transaction as the change | Most changes are not in the tenant store (application files, signed bundles, iyzico); an outbox only for the DB-backed ones would split the mechanism without closing the gap for the rest |
| A middleware that records every non-GET request generically | It knows neither the target's id (created keys, invitations), nor before/after, nor the tenant of a new organization; the route class keeps the discipline central and the detail in the route |
| Explicit `await audit(...)` calls at the end of each route | Easy to forget, and refused attempts happen before the route body runs; the route class records both, and the walk test catches a missing declaration |
| Store the actor's e-mail with the event | Personal data in an append-only table for 400 days; erasure would need an `UPDATE` grant the table must not have |
| Record 401s and validation failures too | No principal (401) or no change and no right refused (422); they belong to process logs and rate limits, not the tenant's audit log |
| Fail the request when the audit write fails | The change has already happened and cannot be rolled back for files or the payment provider; a 500 would invite a retry that repeats it |

## Consequences

- Contracts first: [audit](../contracts/audit.md) 0.1 (new). Permission matrix gains
  `audit.read`.
- Every new changing route of the policy service must declare an audit action and add it to the
  contract's table and the console's labels; `tests/unit/test_audit_routes.py` enforces the
  declaration.
- The bundle change log (`changelog.jsonl`, `history` of a guardrail) stays as the bundle's own
  history; retiring it in favour of audit events (ADR-0030 §7) is not done here.
- The engine API's control endpoints (`/v1/bundles/reload`) and the gateway are not audited
  yet; sign-in events come from the identity service (Auth0) and are not copied.
- Open: the hash chain (ADR-0030 §2), SIEM export (ADR-0030 §5), per-tenant retention within an
  edition's range, recording the retention job itself.

## Validation

- `tests/unit/test_audit_routes.py`: every changing route declares one audit action; each
  action has exactly one route and a target type.
- `tests/integration/test_audit_log.py` (SQLite and PostgreSQL with RLS): a day of changes
  produces the expected events with before/after and no secret; every audited route records a
  refused attempt; a cross-tenant attempt stays in the actor's own log; filters, paging,
  permissions; the CSV export quotes formula cells and is itself recorded; a failed write is
  logged and the change stands; retention keeps 400 days.
- `tests/integration/test_billing.py`: billing changes are audited without billing details.
- `tests/db/test_isolation.py`: `audit_events` is tenant-scoped with forced RLS and append-only
  for the application role.
- Revisit when the first customer asks for SIEM delivery or a longer retention, or when an
  auditor asks for tamper evidence beyond database grants and backups (the hash chain).
