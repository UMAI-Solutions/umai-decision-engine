# ADR-0030 — Audit events and decision events: append-only per tenant, exportable; structured telemetry with an allowlist

- **Status:** Proposed (awaits product-owner approval); §1 decision events, §3 append-only tables and §4 decision-event retention accepted by ADR-0041 (2026-10-26); for the engine and gateway, §3's outbox is replaced by ADR-0042
- **Date:** 2026-10-17
- **Deciders:** engineering proposal; product owner (retention defaults are a product decision)
- **Builds on:** ADR-0005 (no raw PII beyond L0), ADR-0018 (SIEM integration, dashboards, reports), ADR-0019 (usage, SIEM export), tagged-action contract 0.2

## Context

Two things are missing for a customer or an auditor to answer "what happened": decision
events are not stored (`review_queued=False` in the orchestrator; the tagged-action contract
exists without a sink), and configuration changes are logged to `changelog.jsonl` without an
actor (readiness test A1). Logs are plain text with ids only — good for rule 1, useless for
per-tenant questions. SOC 2's system-operations and logical-access criteria, the Enterprise
SIEM row of ADR-0018 and the dashboards/reports rows all need the same two event streams.

## Decision

1. **Two event families, one discipline (no content).**
   - **Decision events**: the tagged-action record (contract 0.2) as already specified —
     entity types, counts, hashes, redacted excerpt, tags, decision, bundle id/version/hash,
     engine version. Never raw content.
   - **Audit events** (new contract `audit-event` 0.1): configuration and access changes —
     API key created/revoked, member invited/removed/role changed, draft published, version
     activated/rolled back (incl. `break_glass`), entitlement or licence changed, SSO/SCIM
     settings changed, retention changed, SIEM destination changed, sign-in success/failure,
     export requested, denied authorization attempts.
2. **Audit event shape.** `event_id` (ULID), `occurred_at`, `tenant_id`, `actor` (`type ∈
   {user, api_key, service, system}`, `id`, `email` for users, `ip`, `user_agent`), `action`
   (dotted verb: `guardrail.version.activated`, `api_key.revoked`, …), `target` (`type`,
   `id`, `version`), `outcome ∈ {success, denied, failure}`, `request_id`, `source`,
   `reason` (free text from the actor, e.g. publish note or break-glass reason), `before` /
   `after` as **hashes or redacted diffs** — a policy diff is allowed (policy ids, actions,
   entity types), content never. `prev_hash` and `hash` form a per-tenant hash chain for
   tamper evidence.
3. **Storage.** Append-only tenant-scoped tables under RLS (ADR-0025). The application role
   has `INSERT` and `SELECT` only on event tables (no `UPDATE`/`DELETE`); retention is applied
   by a scheduled operator job that is itself audited. Writes go through an **outbox** in the
   same transaction as the change, so an event is never lost when the service crashes after the
   change, and are flushed by a worker off the request path (the engine's decision events are
   written by the same outbox pattern into the regional store).
4. **Retention** (proposed defaults, product owner to confirm): audit events 400 days for every
   edition; decision events 30 days Community, configurable 30–400 days Enterprise; per-tenant
   override within the edition's range; erasure of a tenant removes both (ADR-0025 §9).
5. **Access.** Console: an audit-log page (filter by actor, action, target, time) and the
   decisions view for dashboards and reports. API: `GET /v1/audit-events` and
   `GET /v1/decision-events` with cursor pagination, scope `audit:read` / `decisions:read`.
   Export: the SIEM exporter (ADR-0018 `siem` feature; targets Q22) reads the same tables,
   at-least-once, idempotent by `event_id`, per-tenant destination and credential, dead-letter
   on repeated failure. The first targets are **Azure Blob Storage with a locked immutability
   policy** (WORM, JSON lines) and **Event Hubs** for streaming to Microsoft Sentinel and other
   SIEMs (ADR-0031 §8); S3-compatible buckets (Lakera's mechanism) and webhook/HEC follow for
   customers on other clouds.
6. **Structured telemetry with an allowlist.** Process logs become JSON (one record per line)
   with a fixed field set: `ts`, `level`, `logger`, `event`, `request_id`, `tenant_id`,
   `key_id`, `bundle_id`, `bundle_version`, `bundle_sha256`, `decision`, `decided_by`,
   `latency_ms`, `error_type`. Free-text `message` is allowed only for operator events without
   request context. The same allowlist governs OpenTelemetry span attributes and metric labels
   when tracing is added; `tenant_id` is a label on counters, never on high-cardinality
   histograms. Process logs flow to the regional Log Analytics workspace (inside the EU Data
   Boundary) with data-export rules to immutable storage for retention (ADR-0031 §8). FastAPI
   validation errors are rendered by a custom handler that emits `loc` and `type` only
   (readiness GAP-L1). The leak suite runs over logs, traces, metrics text and exported events.
7. **`changelog.jsonl` is retired** in favour of audit events; the policy service's
   `history` is a view over them. Every write endpoint of the policy service and the engine's
   control endpoints emits exactly one audit event, with the actor from the on-behalf-of
   assertion (ADR-0023) or the API key.

## Alternatives considered

| Option | Why not |
|---|---|
| Treat process logs as the audit trail | Not queryable per tenant, mutable, retention tied to the log pipeline, not exportable to a customer |
| A third-party audit-log product for everything (e.g. an identity broker's audit logs) | Fine for console sign-in events as a source, but decision events must stay in region and in our store; one model for both keeps export and reports simple |
| Update-in-place history with "soft deletes" | An auditor wants append-only; `UPDATE`/`DELETE` grants would exist |
| External anchoring (blockchain, notary) | Overkill; a hash chain plus database grants plus backups give tamper evidence |
| Store raw content "for forensics" with strong encryption | Violates ADR-0005; the redacted excerpt and hashes are the forensic record by design |

## Consequences

- Contracts first: `audit-event` 0.1 (new); tagged-action gains `engine_version` (ADR-0019
  Consequences); evaluation API documents `GET /v1/audit-events`, `GET /v1/decision-events`.
- Code: `events/` (outbox, writer, hash chain), `telemetry/` rewritten for JSON logging and the
  allowlist, the validation-error handler, the exporter worker (off the request path), console
  audit page.
- The engine now writes events: a store becomes part of the engine's deployment (ADR-0025) —
  the first time the engine has state outside bundles. Failure of the event store must not
  block decisions: the outbox is local first, flushed asynchronously, with a metric and alert
  on backlog (AGENTS rule 4: the failure is recorded, never silent).
- Dashboards and reports (ADR-0018 rows) become buildable.

## Validation

- Route test: every mutating route in both services declares an audit action and emits one
  event per call (counted in tests); readiness A1 turns from xfail to pass.
- Grant test: the application role cannot `UPDATE` or `DELETE` event rows.
- Hash-chain test: altering a stored event breaks verification of every later event.
- Leak suite: 0 raw values across decision events, audit events, JSON logs, span attributes
  and exported files; GAP-L1 turns from xfail to pass.
- Outbox test: a crash between change and flush loses no event; redelivery creates no
  duplicates (idempotent by `event_id`).
- Reconciliation: for a billing period on a test tenant, decision-event count = usage counter
  = metered quantity (ADR-0028).
- Revisit retention defaults after the first customers' requests, and the SIEM target order
  by demand (Q22).
