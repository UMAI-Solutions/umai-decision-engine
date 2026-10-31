# Contract: audit (audit events, read API, CSV export)

**Version:** 0.1 · **Producer → consumer:** policy service → tenant store (`audit_events`) →
policy service read API → console (Organization → Audit log), auditors (CSV) · **Decisions:**
[ADR-0045](../adr/0045-audit-log-for-configuration-changes.md), [ADR-0030](../adr/0030-audit-events-and-structured-telemetry.md) §1–2, §4, [ADR-0029](../adr/0029-authorization-roles-and-four-eyes.md)

An **audit event** says who changed what in a tenant's configuration, when, and whether it
happened (`success`) or was refused for the caller's rights (`denied`, HTTP 403). The policy
service writes one for every call of a changing route (`POST`, `PUT`, `PATCH`, `DELETE` under
`/v1`), for every audit CSV export and every organization archive, except a person's own console sign-ins (`/v1/me/sessions…`,
`/v1/sessions/check`): they belong to no tenant and are kept in their own records
([console-sessions](console-sessions.md)). Events are append-only: the application role may insert and
read them, never change or delete them (ADR-0030 §3); retention deletes them after 400 days.

**Never in an event:** content (not even redacted), secrets, API-key secrets or hashes,
invitation tokens, checkout tokens, provider keys, card data, billing details (company, tax
number, contact, address), the invitee's e-mail. `details` holds ids and the before/after of
non-secret fields only.

## `AuditEvent`

| Field | Type | Meaning |
|---|---|---|
| `contract_version` | `"0.1"` | |
| `event_id` | string `au-` + 32 hex | Unique per tenant |
| `occurred_at` | RFC 3339, UTC | When the route answered |
| `tenant_id` | string | The tenant the change belongs to (for a new organization: the new one) |
| `actor.type` | `person`, `operator` | A person named by the on-behalf-of assertion, or the control token alone |
| `actor.sub` | string | The person's subject at the identity service (from the assertion) |
| `actor.user_id` | UUID | The person in the tenant store, as member lists name them; absent when the person was not in the store yet |
| `actor.email`, `actor.name` | string | Resolved from the store **when read**, never stored with the event; absent once the person is erased |
| `action` | `AuditAction` | What was done (table below) |
| `target.type` | `TargetType` | What it was done to (fixed per action) |
| `target.id` | string | Its id; absent for a tenant's single object (gateway settings, billing account, subscription, the audit log) |
| `outcome` | `success`, `denied` | `denied`: refused with HTTP 403 (missing role, wrong tenant, a person required, an owner-only change) |
| `request_id` | string ≤ 128 | The caller's `X-Request-Id` (`[A-Za-z0-9._:-]`) or one generated; returned as `X-Request-Id` on success |
| `details` | object, ≤ 4 KB JSON | Per action (table below). A denied event has `{"reason": <error code>}`. Larger details are replaced by `{"truncated": true}` |

```json
{
  "contract_version": "0.1",
  "event_id": "au-5f0c2a9e1b7d4c3a8e6f2b1d0c9a7e5f",
  "occurred_at": "2026-10-30T09:14:03.512000Z",
  "tenant_id": "acme-security",
  "actor": {
    "type": "person",
    "sub": "auth0|ada",
    "user_id": "6f1c2d3e-4b5a-4c6d-8e7f-90a1b2c3d4e5",
    "email": "ada@example.com",
    "name": "Ada"
  },
  "action": "member.update",
  "target": {"type": "member", "id": "0b9a8c7d-6e5f-4a3b-9c2d-1e0f9a8b7c6d"},
  "outcome": "success",
  "request_id": "req-2c4e6a8b0d1f4e3c9a7b5d3f1e0c2a4b",
  "details": {"roles": {"before": ["viewer"], "after": ["admin"]}}
}
```

A refused attempt:

```json
{
  "contract_version": "0.1",
  "event_id": "au-9e8d7c6b5a4f4e3d2c1b0a9f8e7d6c5b",
  "occurred_at": "2026-10-30T09:20:41.003000Z",
  "tenant_id": "acme-security",
  "actor": {"type": "person", "sub": "auth0|vic", "user_id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"},
  "action": "key.revoke",
  "target": {"type": "api_key", "id": "k3f9a2c1"},
  "outcome": "denied",
  "request_id": "req-7d5b3f1e9c8a4b6d2e0f1a3c5e7b9d1f",
  "details": {"reason": "forbidden"}
}
```

## Actions

A route whose permission (ADR-0029, `control_plane/authz.py`) covers one change uses the
permission's name; one whose permission covers several gets a verb under it. One target type per
action.

| `action` | Route | `target.type` / `id` | `details` (success) |
|---|---|---|---|
| `guardrail.draft.create` | `POST /v1/guardrails/{id}/draft` | `guardrail` / bundle id | `base_version` |
| `guardrail.draft.save` | `PUT /v1/guardrails/{id}/draft` | `guardrail` | `problems` (count) |
| `guardrail.draft.discard` | `DELETE /v1/guardrails/{id}/draft` | `guardrail` | — |
| `guardrail.draft.test` | `POST /v1/guardrails/{id}/draft/test` | `guardrail` | `passed`, `total`, `failed` |
| `guardrail.publish` | `POST /v1/guardrails/{id}/draft/publish` | `guardrail` | `version`, `activated`, `previous_active`, `accepted` (case ids), `note` |
| `guardrail.activate` | `POST /v1/guardrails/{id}/activate` | `guardrail` | `version` {before, after}, `note` |
| `tenant.create` | `POST /v1/tenants` | `tenant` / slug | `name`, `region` |
| `tenant.plan` | `PUT /v1/tenants/{id}/plan` | `tenant` | `edition`, `overrides` {before, after} |
| `key.create` | `POST /v1/tenants/{id}/keys` | `api_key` / key id | `name`, `env`, `scopes`, `application_id`, `expires_at` |
| `key.revoke` | `DELETE /v1/tenants/{id}/keys/{key_id}` | `api_key` | `name` |
| `key.update` | `PATCH /v1/tenants/{id}/keys/{key_id}` | `api_key` | `networks` (how many allowed networks; [credentials](credentials.md) 0.9) |
| `key.publish` | `POST /v1/tenants/{id}/keys/publish` | `tenant` | `published` |
| `organization.create` | `POST /v1/organizations` | `organization` / slug | `name`, `region`, `starter_guardrail` |
| `organization.update` | `PATCH /v1/organizations/{id}` | `organization` | `name` {before, after} |
| `member.update` | `PATCH /v1/organizations/{id}/members/{user_id}` | `member` / user id | `roles` {before, after} |
| `member.remove` | `DELETE /v1/organizations/{id}/members/{user_id}` | `member` | `roles` {before, after: null} |
| `member.leave` | `POST /v1/organizations/{id}/leave` | `member` | — |
| `invitation.create` | `POST /v1/organizations/{id}/invitations` | `invitation` / id | `roles`, `expires_at` |
| `invitation.revoke` | `DELETE /v1/organizations/{id}/invitations/{inv}` | `invitation` | — |
| `invitation.resend` | `POST /v1/organizations/{id}/invitations/{inv}/resend` | `invitation` | `replaced_by` (the new invitation's id) |
| `invitation.accept` | `POST /v1/invitations/accept` | `member` / the new member's user id | `roles` |
| `application.save` | `PUT /v1/applications/{id}` | `application` / id | `created`, `guardrail` (new: the id; changed: {before, after}) |
| `application.delete` | `DELETE /v1/applications/{id}` | `application` | — |
| `provider.save` | `PUT /v1/providers/{id}` | `provider` / id | `created`, `credential` (the mode), `enabled`, `default` |
| `provider.delete` | `DELETE /v1/providers/{id}` | `provider` | — |
| `gateway.settings.update` | `PUT /v1/gateway/settings` | `gateway_settings` | `changed` (field names) |
| `billing.account.update` | `PUT /v1/billing/account` | `billing_account` | — (billing details stay in billing) |
| `billing.checkout.start` | `POST /v1/billing/checkout` | `checkout` / checkout id | `edition` |
| `billing.checkout.complete` | `POST /v1/billing/checkouts/{id}/complete` | `checkout` | `status`, `edition` |
| `billing.plan.change` | `POST /v1/billing/plan` | `subscription` | `edition`, `previous_edition` |
| `billing.cancel` | `POST /v1/billing/cancel` | `subscription` | `edition` |
| `webhook.create` | `POST /v1/webhooks/endpoints` | `webhook_endpoint` | `kind`, `event_types` (never the URL: a Slack webhook URL is a credential) |
| `webhook.update` | `PATCH /v1/webhooks/endpoints/{id}` | `webhook_endpoint` | `enabled`, `event_types`, `url_changed` |
| `webhook.delete` | `DELETE /v1/webhooks/endpoints/{id}` | `webhook_endpoint` | — |
| `webhook.secret.rotate` | `POST /v1/webhooks/endpoints/{id}/secret` | `webhook_endpoint` | — (the secret never) |
| `webhook.test` | `POST /v1/webhooks/endpoints/{id}/test` | `webhook_endpoint` | `outcome` |
| `organization.export` | `GET /v1/organizations/{id}/export.zip` | `organization` | `records`, `bytes` (the archive holds member e-mails; [data-export](data-export.md)) |
| `audit.export` | `GET /v1/audit-events/export` | `audit_log` | `rows`, and the export's filters |

Without a `{"before", "after"}` pair a field did not change. A denied event names the target from
the path when the path has one.

**Tenant of a denied event:** a person's is always the tenant their assertion names (an attempt
on another tenant is recorded in the person's own log, never written into someone else's); the
operator's is the path's tenant, else `X-Umai-Tenant`, else the default tenant. A person without
an organization yet (a refused `organization.create`) has no log to write to: the attempt is
logged, not recorded.

## `GET /v1/audit-events` (policy service)

Permission `audit.read`: owner, admin, and the operator. Newest first.

| Query | Meaning |
|---|---|
| `limit` | 1–200, default 50 |
| `cursor` | `next_cursor` of the previous page; a cursor from elsewhere answers 400 `bad_cursor` |
| `since`, `until` | RFC 3339; `since` inclusive, `until` exclusive |
| `actor` | `operator`, a person's `user_id`, or their `sub` |
| `action` | An action, or a prefix of one up to a dot: `billing` matches every `billing.*` action, `guardrail.draft` every draft action. Lower-case letters and dots only (else 422) |
| `target_type`, `target_id` | Exact |
| `outcome` | `success`, `denied` |

```json
{
  "events": [
    {
      "contract_version": "0.1",
      "event_id": "au-3b1d5f7a9c0e4b2d8f6a4c2e0b9d7f5a",
      "occurred_at": "2026-10-30T09:31:12.250000Z",
      "tenant_id": "acme-security",
      "actor": {"type": "operator"},
      "action": "tenant.plan",
      "target": {"type": "tenant", "id": "acme-security"},
      "outcome": "success",
      "request_id": "req-0f1e2d3c4b5a49687f6e5d4c3b2a1908",
      "details": {"edition": {"before": "community", "after": "pro"}}
    }
  ],
  "next_cursor": "MjAyNi0xMC0zMFQwOTozMToxMi4yNTAwMDArMDA6MDB8M2IxZDVmN2E"
}
```

## `GET /v1/audit-events/export` (policy service)

Permission `audit.read`. The same filters as the list (no `limit`, no `cursor`); answers
`text/csv; charset=utf-8` as an attachment `audit-<tenant>-<UTC stamp>.csv`, newest first, at most
50 000 rows. `X-Umai-Export-Rows` gives the number of rows; `X-Umai-Export-Truncated: true` says
more events matched (narrow the time window). The export is itself recorded (`audit.export`).

Columns, in order: `occurred_at`, `event_id`, `actor_type`, `actor_user_id`, `actor_sub`,
`actor_email`, `action`, `target_type`, `target_id`, `outcome`, `request_id`, `details` (JSON,
keys sorted). A cell that a spreadsheet would read as a formula (starting with `=`, `+`, `-`, `@`,
tab or carriage return) is prefixed with `'`.

## Changelog

- 0.1 (2026-10-30, ADR-0045): initial.
