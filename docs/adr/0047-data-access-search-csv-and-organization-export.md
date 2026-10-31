# ADR-0047 — Data access for organizations: decision search, CSV exports and the organization archive

- **Status:** Proposed
- **Date:** 2026-10-30
- **Deciders:** engineering (proposed); product owner to accept
- **Builds on:** ADR-0005 (no content beyond L0), ADR-0025 §9 (tenant-level export, the export
  half), ADR-0029 (permission matrix), ADR-0041 (records and their read API), ADR-0044 (billing
  records)
- **Contracts:** tagged-action 0.6, data-export 0.1 (new), organizations 0.5

## Context

The Cloud SaaS checklist asks for *search* over an organization's data and the *export* half of
*data rights*. Three needs:

1. **Find one decision.** A developer holds the id an application received: the evaluation's
   `event_id`, the caller's own `request_id`, or the gateway call's `gc-…` id. The Detections
   list can only be filtered by period, decision, source, application, phase and review.
   Investigators also ask "where did we see TCKNs?" and "what did policy X decide?": both are in
   the event (`entities[].type`, `matched[].id`) but not filterable.
2. **Take records to a spreadsheet.** A DPO wants the decisions of a period; finance wants a
   month of usage per application. Both are records UMAI already keeps (ADR-0041), with no
   content.
3. **Take the organization out.** An owner may ask for everything UMAI holds for the
   organization: GDPR Art. 20 for the personal data in it (members' e-mails), the EU Data Act's
   switching duties for cloud services, and plain customer trust. ADR-0025 §9 made export a
   tenant-level operation; nothing builds it.

Constraints: no content exists to export (ADR-0005, ADR-0041 §2) and no secret may leave
(API-key hashes, invitation tokens, checkout tokens, provider keys); reads stay inside the tenant
session (RLS); AGENTS rule 6 (no index without a measurement); the policy service is one process
and synchronous work must be bounded; the console relays everything server-side.

## Decision

### D1 — Search and filters on the existing read API

`GET /v1/decision-events` and `GET /v1/decision-events/summary` take three more filters
(tagged-action 0.6), combined with the others (AND), and the CSV export takes the same set:

| Filter | Matches |
|---|---|
| `q` | An id, exactly (leading and trailing spaces dropped): `event_id`, `request_id` or `gateway_call_id`. A gateway call id finds every check of the call |
| `entity_type` | Events in which the type was found (`entities[].type`) |
| `policy_id` | Events the policy helped decide (`matched[].id`). Shadow L1b policies never decide; their outcomes are L1b outcome records |

Exact matching only: ids are opaque and pasted, not typed; prefix and fuzzy search would make
every search a scan with no gain.

### D2 — No new index; two query fixes instead (measured)

Measured on PostgreSQL 17 (Docker on a developer laptop shared with other workloads), as the
application role with RLS, server-side times from `EXPLAIN ANALYZE` (median of 3):

| Query | 100 000 events in the tenant | 1 000 000 events |
|---|---|---|
| First page, no filter (baseline) | 0.5 ms | 0.6 ms |
| `q` = event, request or gateway call id (also unknown ids) | 68–94 ms | 200–390 ms |
| `entity_type`, common type, first page (30 days) | 3 ms | 3 ms |
| `entity_type`, type no event has, first page (30 days / 7 days) | 738 ms / 280 ms | 11.9 s |
| `policy_id`, first page (30 days) | 7 ms | 2.6 s |
| Bounded count for the CSV cap, `entity_type`, 30 days | 800 ms | 8–13 s |
| Summary counts, 30 days / with `entity_type` | 595 ms / 828 ms | 2.7 s / 12 s |
| CSV batch of 1 000 rows, keyset as before / with the bound (below) | 20 ms / 5.7 ms | 841 ms / 8.3 ms |

At the volumes UMAI has and expects for the next plans (Community 10 000 requests a month,
30-day retention: under 50 000 events) every query stays under one second without an index, so
none is added. The JSON filters scan the tenant's window; at a million events in it they take
seconds. **Revisit** when a tenant holds more than 200 000 events in its retention window, or
the filtered summary's p95 passes 2 s in production: then GIN expression indexes on
`(entities::jsonb)` and `(matched::jsonb)` queried with containment, and b-tree indexes on
`(tenant_id, request_id)` and `(tenant_id, gateway_call_id)` when the id search passes 1 s.

Two changes the measurements did call for:

- **Keyset pages carry a redundant `occurred_at <= cursor` bound.** The previous
  `occurred_at < t OR (occurred_at = t AND id < i)` alone kept PostgreSQL from bounding the index
  scan: every page re-read the window (841 ms per 1 000-row page at 1 M events, 8 ms with the
  bound). The list's "Show more" gets the same fix.
- **The summary counts days, decisions and sources in one grouped query** instead of two.

A `LIKE` pre-filter on the JSON text before the exact JSON test was measured and dropped: no
gain where nothing matches (7.8 s against 8.6 s) and one case slower (a common type's first page,
3 ms → 640 ms).

### D3 — CSV exports, streamed, refused above a cap instead of cut

- **Format:** RFC 4180 (comma, `"` quoting, CRLF), UTF-8 with a byte-order mark so spreadsheet
  programs read Turkish characters correctly; timestamps ISO 8601 UTC (`…Z`); list cells are
  space-separated tokens (`TR_TCKN:2 EMAIL_ADDRESS:1`). A cell starting with `=`, `+`, `-`, `@`,
  tab or CR gets a leading apostrophe: request ids and subject fields come from callers, and a
  spreadsheet would run them as formulas (CSV injection).
- **Decisions CSV** (`GET /v1/decision-events/export.csv`): the list's filters, newest first,
  streamed in batches of 1 000, one short tenant session per batch; `until` defaults to the
  request time so the count and the file cover the same events. **Cap: 100 000 rows.** The
  matching events are counted first (a count bounded at cap + 1); above the cap the request is
  refused with 422 `export_too_large` before the first byte. A file is never cut short, because a
  truncated file looks complete in a spreadsheet.
- **Usage CSV** (`GET /v1/usage/export.csv?month=YYYY-MM`): the month's ledger **per UTC day and
  dimension** (meter, source, environment, application, key, provider, model) with the quantity
  and the number of ledger records summed. Not raw ledger rows: one gateway call writes about
  eleven of them (three call meters, four per check), so a Community organization reaches
  100 000 raw rows within a month; per-request detail is what the decisions CSV is for. Cap
  100 000 lines (31 days × the dimensions; not reached in practice), refused the same way.
- **Permissions:** the reads' (`records.decisions.read`, `records.usage.read`): billing exports
  usage, not decisions.

Measured end to end through the policy service (same laptop): a 99 796-row decisions CSV (30 MB)
in 19 s; a month's usage CSV from 1 000 000 ledger rows in 2.5 s (5 040 lines).

### D4 — The organization archive: one ZIP of JSON files, owners only, bounded

`GET /v1/organizations/{id}/export.zip` returns one ZIP:

| File | Holds |
|---|---|
| `manifest.json` | Format and version, organization, region, when and by whom, engine version, retention in force, every file with its record count, what is not included |
| `organization.json` | The organization, its accepted terms, plan limits and operator overrides |
| `members.json` | Members (e-mail, name, roles, state, joined) and invitations (e-mail, roles, state, dates) |
| `api_keys.json` | Key metadata: id, name, environment, scopes, application, dates, status |
| `applications.json` | Gateway defaults, providers (the built-in one included) and applications |
| `guardrails/…` | Every published version as stored (`bundle.yaml`, signed `manifest.json`, golden tests), the change log, an open draft, and an index with the live version |
| `records/decision_events.jsonl`, `records/l1b_outcomes.jsonl` | Records in their contract shape (tagged-action), newest first |
| `records/usage_daily.jsonl` | The whole retained ledger as daily lines (D3) |
| `billing.json` | Billing account, subscriptions and billing events |

- **ZIP of JSON files, not one JSON document:** a single document must be read whole (108 MB of
  JSON for 200 000 records); the ZIP compresses it to 8.2 MB, its files open independently,
  records are JSON Lines, and guardrail versions keep their signed form, so they can be verified
  and imported again.
- **Owners only** (new permission `organization.export`): the archive holds every member's
  e-mail and the billing account. Admins hold every non-owner permission without `rbac`
  (ADR-0029 §2), so the permission is owner-only on purpose. The control token alone may export
  only where assertions are optional (single-operator installs).
- **Bounded and synchronous (v1):** at most **250 000 records** (decision events plus L1b
  outcomes, counted before anything is built); above it 422 `export_too_large`. Built in a
  spooled temporary file (in memory up to 32 MiB, then on disk) before the first byte is sent,
  one export per organization at a time per process (409 `export_running`). Measured: 200 000
  records (50 000 events, 150 000 outcomes, 150 000 ledger rows) in 23.5 s, 8.2 MB.
- **Nothing secret, by allow-list:** fields reach the archive only when named in code (billing
  tables) or through a view that never had them (key records, member and invitation views).
  Not included: content (never recorded), API-key secrets and hashes, invitation link secrets
  and hashes, provider keys (the deployment holds them; a provider names one at most), signing,
  HMAC and assertion keys, checkout tokens, payment card data (iyzico's hosted form takes it;
  UMAI never sees it).

### D5 — Out of scope

- **Erasure.** Billing records (account, subscriptions, payments) fall under statutory retention
  for commercial and tax records, so an organization's data cannot simply be deleted on request;
  which records, for how long and what replaces them (anonymisation, restricted access) is a
  legal question (Q61). Decision events and usage are already deleted by retention (ADR-0041 §5).
- **Import and bulk operations** (guardrails, applications, members): next checklist item; the
  archive's layout is meant to be readable by it.
- **Asynchronous exports** (a job and a download link): when real organizations reach the
  limits above.
- **Audit:** every export is logged (`records.exported`, `organization.exported`: tenant,
  actor, rows, bytes; no content). It becomes an audit event when the audit log lands
  (ADR-0030, built in parallel).

## Alternatives considered

| Option | Why not |
|---|---|
| Separate `event_id`, `request_id`, `gateway_call_id` parameters | One search box in the console; callers rarely know which kind of id they hold. The three columns are compared in one query |
| Indexes for the new filters now | Measured unnecessary at today's and planned volumes (D2); each index costs every write on the busiest table. The trigger is recorded |
| Truncate a CSV at the cap and say so in a header | A downloaded file loses its headers; a cut file looks complete. Refusing first is honest and cheap (bounded count) |
| Semicolon-separated CSV for Turkish Excel | Excel's list separator follows the Windows locale; a semicolon file then breaks every other tool, and decimals clash with the decimal comma. RFC 4180 with a BOM opens correctly through Excel's import and in every other tool |
| Raw ledger rows in the usage CSV | ~11 rows per gateway call: a Community month passes any sane cap (D3) |
| One JSON document for the organization | Must be parsed whole; no compression; guardrails lose their signed files (D4) |
| Asynchronous export with storage and a link now | Needs a job runner, object storage and expiring links: too much for v1 when 250 000 records build in under 30 s |
| Admins may export | The archive carries every member's e-mail and the billing account; GDPR Art. 20 requests come through the account holder |

## Consequences

- New routes in the policy service; the console gets a search box and filters on Detections,
  "Download CSV" on Detections and Usage, and "Export organization data" for owners.
- Faster list pagination for everyone (keyset bound) and one query less per summary.
- Exports are CPU work in the policy service (pydantic validation, CSV and JSON); per-batch work
  runs in a worker thread so other requests keep being served, and the caps bound it. The
  console's relay needs a longer timeout for these downloads than for JSON calls.
- The archive layout is a contract (data-export 0.1); import will be built against it.

## Validation

- `tests/integration/test_data_access.py`: search finds a decision by each id (and not another
  tenant's), entity and policy filters, the summary follows them, CSV columns, escaping, Turkish
  characters and caps, permissions, archive contents, nothing secret in it (the real key secret,
  invitation token and checkout token of the test are searched for in every file), owner-only,
  limits and the running guard. `tests/db/test_records_queries.py`: the JSON filters, id search
  and usage lines on PostgreSQL under RLS.
- Revisit D2 at the trigger above; revisit the caps and the synchronous archive when an
  organization is refused by them.
