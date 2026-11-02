# ADR-0055 — Overview figures: per application, latency percentiles, Playground runs left out

- **Status:** Proposed
- **Date:** 2026-11-02
- **Deciders:** product owner (asked for the dashboard on 2026-11-02), engineering

## Context

The console's Overview was the three setup steps and a list of applications. The product owner
asked for a security dashboard (W1 in `docs/architecture/console-information-architecture.md`):
requests checked, blocked, masked and flagged against the previous period, latency, decisions per
day, what was found, the policies that decided, a table per application and recent detections,
plus a *Create your first application* card for a new organization.

The decision-event summary (tagged-action 0.5, ADR-0047 D2) already counted per day, decision and
source, and the entity types and policies of the newest 20 000 events. It had no per-application
counts and no latency, although every event stores `latency_total_ms`, `latency_l0_ms` and
`latency_l1b_ms` as columns.

Playground runs are recorded like any evaluation. The console calls `POST /v1/evaluate` from its
server with the **operator token** (no key, so `key_id` and `application_id` are `null`) and gives
each run the request id `console-<uuid>` (umai-control-center, `/api/engine/evaluate`); the
browser sets neither. They count toward usage and the live quota (ADR-0043 D6). On SaaS only the
console and UMAI's operators hold the operator token; a self-hosted install may send its real
traffic with it (ADR-0023 kept the shared token for self-hosted).

## Decision

1. **D1 — The summary counts per application.** `by_application[]`: per `application_id` (and
   `null` for the events without one) the decisions and the newest event in the window, exact.
   The application joins the group key of the existing one-pass count query (days × decisions ×
   sources × applications rows, at most a few thousand).
2. **D2 — Exact latency percentiles.** `latency_ms.{total,l0,l1b}`: `events`, `p50`, `p95`, each
   over the window's events that recorded that latency. Nearest rank: PostgreSQL's
   `percentile_disc`; SQLite (development) has no ordered-set aggregates and takes the same rank
   by `ORDER BY … OFFSET`, so both stores return the same value.
3. **D3 — The summary plans for its window.** On PostgreSQL the summary's transaction sets
   `plan_cache_mode = force_custom_plan`. A pooled connection's prepared statements switch to a
   generic plan after five runs; for the summary that plan is an index scan sized for ~1 800 rows
   (`EXPLAIN (GENERIC_PLAN)`), which over a 30-day window of a busy tenant reads the heap row by
   row. Planning per query costs 0.2–0.4 ms (1.7 ms the first time on a connection).
4. **D4 — Playground runs are identified by the server and left out of the Overview.** A
   Playground run is an event of the evaluation API (`source: api`) admitted with the operator
   token (`key_id: null`) whose `request_id` starts with `console-`. Filter `playground`
   (`true` / `false`) on the list, the summary and the CSV export; the Overview asks with
   `playground=false`. An API key's events are never Playground runs, whatever request id the
   caller sends, so no customer can hide traffic this way; and the filter only changes a view:
   usage, quotas and invoices read the usage ledger, untouched.
5. **D5 — `action` may be repeated** on the list, the summary and the export (any of the
   decisions), so "recent detections" (everything but a plain allow) is one call.

## Alternatives considered

| Option | Why not |
|---|---|
| Leave every operator-token event out (`key_id: null` on the API) | On a self-hosted install the operator token may carry real traffic; the Overview would hide it by default |
| A flag in the evaluation request ("this is a playground run") | Set by the caller: any key holder could mark real traffic and keep it off dashboards, and later off usage if the flag were reused there. The brief rules it out |
| A credential of the console's own, recorded on the event (`source: console` or a `playground` field) | The robust form: no naming convention between two repositories. Needs a second operator credential in the engine and on every install, the tagged-action and usage `source` enums, and a decision on whether such runs count toward quotas. **Left for the product owner** (below) |
| Approximate percentiles (a log-scale histogram in SQL, or the 20 000-event sample the top lists use) | Exact costs one more pass over the window (measured below) and needs no error bound in the UI; revisit with the trigger in D2 of ADR-0047 |
| `percentile_cont` (interpolated) | A value no event took; `percentile_disc` also matches SQLite's offset exactly, so tests hold on both stores |
| A separate per-application query | A second pass over the window; adding the column to the existing group key costs 0–15 % of that query (measured below) |
| Turning off the prepared-statement cache for the whole pool | Every query of the policy service would be planned per call; the generic plan is right for the paged list (always bounded by `LIMIT`) |
| The previous period inside the summary (`previous_*` fields) | The console asks twice with an earlier `since` / `until`; no contract change |

**For the product owner:** if Playground runs should be told apart without the `console-`
convention, the engine would accept a second operator credential held only by the console and
stamp its events (a tagged-action and usage change, plus `UMAI_DE_CONSOLE_TOKEN` on every
install). Whether Playground runs then stop counting toward the monthly quota is a pricing
decision (ADR-0043 D6 counts them today).

## Consequences

- The console's Overview reads two summaries (this period and the one before) and one list page
  per period change; Detections opens on the filters in its address (`?q=`, `?entity_type=`,
  `?policy_id=`, `?application_id=`, `?period=`).
- The summary does one more full pass over the window (the percentiles). Measured below; at the
  volumes of ADR-0047 D2 (under 50 000 events per tenant and month) it stays well under a second.
- The `console-` prefix is now a contract (tagged-action 0.7): the console must keep it for
  Playground runs, and the engine never treats an API key's events as Playground runs.

## Validation

- `tests/integration/test_records_api.py` (SQLite) and `tests/db/test_records_queries.py`
  (PostgreSQL, application role under forced RLS): per-application counts and newest event,
  percentiles with exact expected values on both stores, the Playground filter (an API key with a
  `console-` request id is not a Playground run; the prefix is case-sensitive), the repeated
  `action`, and a query naming another tenant inside a tenant's session finding nothing.
- `dev/bench_summary.py`: the summary as the policy service runs it, before and after, on
  synthetic events (below). Revisit when a tenant's 30-day summary passes 2 s p95 in production
  (ADR-0047 D2's trigger): then pre-aggregate per day and application, or approximate the
  percentiles.

### Measured (2026-11-02)

PostgreSQL 17 (Docker Desktop on a Windows laptop shared with other work), application role under
RLS, synthetic events over the last 30 days in one tenant (eight applications, 85 % allowed, L1b
on 30 %), the table holding 1.1 M events of three tenants. `RecordsReader.summary` timed from
Python as the policy service calls it, after one warm-up, through a pooled connection; median of
three interleaved rounds of five calls each (range of the rounds' medians):

| Events in the tenant | Window | Before (main, 2e53478) | After | After, `playground=false` |
|---|---|---|---|---|
| 100 000 | 24 h | 41 ms (41–50) | 59 ms (57–59) | 76 ms (63–78) |
| 100 000 | 7 d | 451 ms (433–454) | 513 ms (435–539) | 525 ms (449–528) |
| 100 000 | 30 d | 583 ms (581–592) | 855 ms (764–873) | 858 ms (716–900) |
| 1 000 000 | 24 h | 537 ms (527–550) | 690 ms (571–690) | 684 ms (566–704) |
| 1 000 000 | 7 d | 1 227 ms (1 190–1 299) | 1 398 ms (1 323–1 460) | 1 384 ms (1 369–1 488) |
| 1 000 000 | 30 d | 4 013 ms (3 980–4 328) | 3 343 ms (3 264–3 428) | 3 421 ms (3 352–3 432) |

Server side (`psql`, 30 days): the one-pass count with the application in its group key
1.03–1.17 s at 1 M events (0.93–1.17 s without it); the percentile query 1.0 s for the total alone
and 1.7 s for total, L0 and L1b (70 ms and 115 ms at 100 000 events). Without D3 the after column
at 1 M / 30 d was 8.1 s: the generic plan's index scan. The Overview asks for two periods at once,
so its wait is the slower of the two calls.
