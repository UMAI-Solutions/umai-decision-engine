# Contract: data exports

**Version:** 0.1 · **Producer → consumer:** policy service → console → people (spreadsheets,
archives) · **Decision:** [ADR-0047](../adr/0047-data-access-search-csv-and-organization-export.md)
· **Models:** `src/umai_decision_engine/contracts/exports.py`; routes: `control_plane/app.py`,
writers: `control_plane/exports.py`

**Implementation status (2026-10-30):** built; the console's Detections, Usage and Settings pages
download them.

Three exports of what UMAI keeps for an organization. None holds content: UMAI never records it
(ADR-0005, ADR-0041 §2). None holds a secret or a secret's hash. Every export is read through the
tenant session (ADR-0025 §9) and logged with tenant, actor and size.

## 1. Decisions CSV

`GET /v1/decision-events/export.csv` takes the filters of `GET /v1/decision-events`
([tagged-action](tagged-action.md) 0.6: `since`, `until`, `action`, `source`, `application_id`,
`phase`, `review`, `entity_type`, `policy_id`, `q`) and returns the matching decision events,
newest first, streamed. `until` defaults to the time of the request. Permission
`records.decisions.read` (owner, admin, policy editor, approver, viewer; the operator token with
`X-Umai-Tenant`).

**Cap: 100 000 rows.** The matching events are counted first; above the cap the request is
refused with 422 `export_too_large` (§4) before anything is sent. A file is never cut short.

```csv
occurred_at,event_id,request_id,gateway_call_id,source,environment,application_id,key_id,phase,action,severity,decided_by,degraded,floor,would_have,guardrail,guardrail_version,guardrail_mode,entities,tags,policies,review,review_reasons,errors,latency_ms,latency_l0_ms,latency_l1b_ms,subject_role,subject_department,destination_app,destination_class,agent_tool,agent_server
2026-10-30T08:15:00Z,ta-5d1c0b9a8e7f4a3b2c1d0e9f8a7b6c5d,gw-2c4e6a8b0d1f3a5c7e9b1d3f5a7c9e1b,gc-8f7e6d5c4b3a29180716253443526170,gateway,live,main,AbCd1234,PRE_LLM,BLOCK,HIGH,L0,false,,,gr-demo-data-protection,3,ENFORCE,TR_TCKN:2,data.pii.tr_tckn,l0_policy:identity-data,false,,,3.4,3.1,,satış,"Çağrı Merkezi, İzmir",chatgpt.com,external_llm,,
```

| Column | From the decision event |
|---|---|
| `occurred_at` | ISO 8601, UTC (`…Z`) |
| `event_id`, `request_id`, `gateway_call_id`, `source`, `environment`, `application_id`, `key_id`, `phase` | As in the event |
| `action`, `severity`, `decided_by`, `degraded`, `floor`, `would_have` | `decision` |
| `guardrail`, `guardrail_version`, `guardrail_mode` | `bundle.id`, `bundle.version`, `bundle.mode` |
| `entities` | `TYPE:count` per entity type, space-separated |
| `tags` | Tag codes, space-separated |
| `policies` | `kind:id` of `matched[]`, space-separated |
| `review`, `review_reasons` | Reasons space-separated |
| `errors` | `layer:type`, space-separated (never a message) |
| `latency_ms`, `latency_l0_ms`, `latency_l1b_ms` | `latency_ms.total`, `.l0`, `.l1b` |
| `subject_role`, `subject_department`, `destination_app`, `destination_class`, `agent_tool`, `agent_server` | `subject`, `destination`, `agent_action` |

## 2. Usage CSV

`GET /v1/usage/export.csv?month=YYYY-MM` (default: the current month, UTC) returns the month's
[usage ledger](usage.md) **per UTC day and dimension**: one line per day, meter, source,
environment, application, key, provider and model, with the summed `quantity` and the number of
ledger `records` in it, oldest first. Permission `records.usage.read` (as `GET /v1/usage`,
billing included). **Cap: 100 000 lines**, refused like §1 (not reached in practice: 31 days ×
the dimensions).

```csv
date,meter,quantity,records,source,environment,application_id,key_id,provider,model
2026-10-30,evaluations,640,640,gateway,live,main,AbCd1234,,
2026-10-30,provider_input_tokens,92220,210,gateway,live,main,AbCd1234,openai,gpt-4.1-mini
```

One line of this shape (`UsageLine`) is also a line of `records/usage_daily.jsonl` in the
archive (§3):

```json
{ "date": "2026-10-30", "meter": "provider_input_tokens", "quantity": 92220, "records": 210, "source": "gateway", "environment": "live", "application_id": "main", "key_id": "AbCd1234", "provider": "openai", "model": "gpt-4.1-mini" }
```

## 3. Organization archive

`GET /v1/organizations/{id}/export.zip` returns one ZIP (`application/zip`) of JSON files.
Permission `organization.export`: **owners only** (it holds every member's e-mail and the billing
account); the operator token alone only where assertions are optional. The archive is built
before the first byte is sent.

**Limit: 250 000 records** (decision events plus L1b outcomes, counted first); above it 422
`export_too_large`. One export per organization at a time (409 `export_running`).

| Path | Content |
|---|---|
| `manifest.json` | `ArchiveManifest` (below): every file with its record count, what is not included |
| `organization.json` | The organization ([organizations](organizations.md) `Organization`), `terms_version`, `terms_accepted_at`, `limits` ([entitlements](entitlements.md)), `limit_overrides` |
| `members.json` | `{"members": [Membership…], "invitations": [Invitation…]}` as the organizations contract shows them |
| `api_keys.json` | `{"keys": [key record + "status"]}` ([credentials](credentials.md) key records) |
| `applications.json` | `{"valid", "gateway", "providers", "applications"}` as `GET /v1/gateway/settings`, `/v1/providers` and `/v1/applications` show them; `valid: false` when the stored file would be refused, which is then exported as stored |
| `guardrails/index.json` | `{"guardrails": [{"id", "active_version", "versions", "draft"}]}` |
| `guardrails/<id>/<version>/…` | Each published version as stored: `bundle.yaml`, the signed `manifest.json`, `tests/*.yaml` ([policy-bundle](policy-bundle.md)) |
| `guardrails/<id>/changelog.jsonl`, `guardrails/<id>/draft.json` | Publishes and activations with their actor; the open draft |
| `records/decision_events.jsonl` | Decision events ([tagged-action](tagged-action.md)), one per line, newest first |
| `records/l1b_outcomes.jsonl` | L1b outcome records, newest first |
| `records/usage_daily.jsonl` | The retained ledger per day and dimension (§2), oldest first |
| `billing.json` | `{"account", "subscriptions", "events"}`: the billing details, subscriptions (provider references, edition, status, periods) and billing events (kind, references, amount, currency, actor, detail) |

```json
{
  "format": "umai-organization-export",
  "contract_version": "0.1",
  "organization": "acme-security",
  "region": "eu",
  "generated_at": "2026-10-30T18:20:00Z",
  "generated_by": { "type": "person", "sub": "auth0|olga" },
  "engine_version": "0.1.0",
  "retention_days": { "decision_events": 30, "l1b_outcomes": 30, "usage": 400 },
  "files": [
    { "path": "organization.json" },
    { "path": "members.json", "records": 3 },
    { "path": "api_keys.json", "records": 2 },
    { "path": "applications.json", "records": 1 },
    { "path": "guardrails/gr-acme-data-protection/1/bundle.yaml" },
    { "path": "guardrails/index.json", "records": 1 },
    { "path": "records/decision_events.jsonl", "records": 1840 },
    { "path": "records/l1b_outcomes.jsonl", "records": 5210 },
    { "path": "records/usage_daily.jsonl", "records": 96 },
    { "path": "billing.json", "records": 4 },
    { "path": "manifest.json" }
  ],
  "not_included": [
    "Request and answer content: UMAI never records it (ADR-0005, ADR-0041).",
    "API key secrets and their hashes: a secret is shown once, at creation."
  ]
}
```

`records` is the number of lines of a `.jsonl` file, or of entries in the file's main list
(members, keys, applications, guardrails, billing events).

**Not included:** request and answer content (never recorded); API-key secrets and their hashes;
invitation link secrets and their hashes; provider keys (held by the deployment, never per
organization; a provider names one at most); signing, HMAC and assertion keys; hosted-checkout
tokens; payment card data (iyzico's hosted form takes it, UMAI never receives it); records past
their retention period.

## 4. Errors

Bodies are `{"detail": {"error": "<code>", "detail": "<sentence>", …}}` like every
policy-service refusal.

| HTTP | `error` | When |
|---|---|---|
| 403 | `forbidden`, `actor_required`, `tenant_mismatch` | The roles do not allow the export; the control token alone where a person is required; another organization |
| 404 | `not_found` | No such organization |
| 409 | `export_running` | An archive of this organization is being built |
| 422 | `export_too_large` | More rows or records than the cap match; `limit` names it |
| 422 | (validation) | A malformed filter or month |

```json
{ "detail": { "error": "export_too_large", "detail": "more than 100000 decisions match; choose a shorter period or add filters", "limit": 100000 } }
```

## 5. Format rules

- CSV: RFC 4180 (comma, `"` quoting, CRLF), UTF-8 with a byte-order mark; booleans `true` /
  `false`; empty cell for null; numbers with a decimal point. A cell that starts with `=`, `+`,
  `-`, `@`, tab or CR gets a leading `'` so spreadsheets do not run it as a formula. In Excel with
  a non-comma list separator (Turkish regional settings), open the file through *Data › From
  Text/CSV*.
- JSON: UTF-8, not ASCII-escaped. Timestamps ISO 8601 UTC.
- File names: `umai-decisions-<organization>-<yyyymmddThhmmZ>.csv`,
  `umai-usage-<organization>-<yyyy-mm>.csv`, `umai-<organization>-export-<yyyymmddThhmmZ>.zip`.

## Changelog

- 0.1 (2026-10-30): decisions CSV, usage CSV per day and dimension, organization archive
  (ADR-0047).
