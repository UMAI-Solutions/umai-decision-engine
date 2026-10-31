# Contract: console sessions (revocable sign-ins)

**Version:** 0.1 · **Producer → consumer:** console ↔ policy service (`/v1/me/sessions…`,
`/v1/sessions/check`) · **Decisions:** [ADR-0024](../adr/0024-human-identity-oidc-keycloak.md)
§2 and its amendment of 2026-10-30 · **Related:** [credentials](credentials.md) 0.8 (the
assertion's `session_id`), [organizations](organizations.md)

Every console sign-in has a record in the policy service. The console keeps the session id in
its encrypted cookie and sends it as the on-behalf-of assertion's `session_id`; the policy
service stores only its SHA-256 and refuses calls whose session has ended. The console's
middleware asks the policy service whether the session is alive (cached 30 s), so ending a
session signs that browser out.

## Session id

256 random bits, base64url without padding (43 characters), made by the console at
`/auth/callback`. Pattern `^[A-Za-z0-9_-]{32,128}$`. Never stored by the policy service (only
`sha256(session_id)`, hex), never logged, never shown.

## Console session — `ConsoleSession`

```json
{
  "id": "3c0d6c1e-7a52-4f0e-9a43-5b1d2e3f4a5b",
  "device": "Firefox on Windows",
  "created_at": "2026-10-30T08:00:00Z",
  "last_seen_at": "2026-10-30T09:15:00Z",
  "expires_at": "2026-10-30T20:00:00Z",
  "current": true
}
```

| Field | |
|---|---|
| `id` | Our id of the record (UUID): what a person ends a session by. Not the session id |
| `device` | Coarse label from the console (`<browser> on <system>`, at most 64 characters, letters, digits, spaces and `.()-/`); `null` when the console could not tell. No user agent, no IP address |
| `created_at`, `last_seen_at`, `expires_at` | Sign-in, last activity (written at most every 5 minutes), absolute end (sign-in + 12 h) |
| `current` | The session of the calling assertion |

Lists show live sessions only: not revoked, before `expires_at`, seen within the idle limit
(70 minutes, `UMAI_DE_CONSOLE_SESSION_IDLE_MINUTES`).

## Requests

`SessionStart` (body of `POST /v1/me/sessions`; the session id is the assertion's `session_id`):

```json
{ "device": "Firefox on Windows" }
```

`SessionCheck` (body of `POST /v1/sessions/check`, the console middleware; `tenant_id` is the
session's active organization, absent before one is chosen):

```json
{ "session_id": "q3Jt0yY3m7xQ2bQe6l1oZp8m0WcH4n5sV9aK2dF7gR1", "subject": "auth0|6512f0c0a1b2c3d4e5f60718", "tenant_id": "acme-security" }
```

`SessionStatus` (its answer):

```json
{ "state": "active", "member": true }
```

| `state` | Meaning | Console |
|---|---|---|
| `active` | A live record of this person | Serve the request |
| `ended` | Revoked, past `expires_at`, idle too long, or another person's | Signed out |
| `unknown` | No record with this id | Signed out |

`member`: with `tenant_id` and `state` `active`, whether the person is an active member of that
organization and the organization is active; `null` otherwise. `false`: the console drops the
organization from the session (the person was removed, or the membership or organization was
suspended).

## Routes

| Route | Action (permission matrix) | Does |
|---|---|---|
| `POST /v1/me/sessions` | `me.sessions.start` (self-service; the only person call allowed before the record exists) | Start: records the assertion's `session_id` for the person; 201 `ConsoleSession`. Beyond 10 live sessions of the person (`UMAI_DE_CONSOLE_SESSION_LIMIT`) the least recently seen ends |
| `GET /v1/me/sessions` | `me.sessions` (self-service) | `{"sessions": [ConsoleSession]}`, live ones, most recently seen first |
| `DELETE /v1/me/sessions/current` | `me.sessions` | Sign-out: ends the assertion's session; 204 |
| `DELETE /v1/me/sessions/{id}` | `me.sessions` | Ends one of the person's sessions; 204. The current one is allowed (= sign-out) |
| `POST /v1/me/sessions/revoke-others` | `me.sessions` | "Sign out other devices": ends every live session of the person but the current; `{"revoked": n}` |
| `POST /v1/sessions/check` | `session.check` (operator: the console's service credential without an assertion; never a person) | `SessionStatus`; updates `last_seen_at` (at most every 5 minutes) |

Revocation reasons stored with the record (`revoke_reason`, not in the API yet): `signed_out`,
`revoked` (ended from another session), `signed_out_elsewhere` (revoke-others), `limit`.

## Every call with an assertion

The policy service checks the assertion's `session_id` on every route except `me.sessions.start`:

| `UMAI_DE_CONSOLE_SESSIONS` | A live record of the person | An ended record, or another person's | No record |
|---|---|---|---|
| `required` (default in production) | allowed | 401 `session_ended` | 401 `session_ended` |
| `optional` (default elsewhere) | allowed | 401 `session_ended` | allowed |

The control token without an assertion (the operator) has no session and is not checked.

## Errors

Bodies are `{ "detail": { "error": "<code>", "detail": "<sentence>" } }`.

| HTTP | `error` | When |
|---|---|---|
| 401 | `session_ended` | the assertion's session ended, was never recorded (`required`), or belongs to another person. The console signs the browser out |
| 403 | `actor_required` | a self-service session route without a person |
| 403 | `forbidden` | `session.check` with an assertion (a person) |
| 404 | `not_found` | ending a session that is not one of the person's live sessions |
| 409 | `session_exists` | starting a session whose id is already recorded |
| 422 | (validation) | a malformed body: `device` or `session_id` outside the patterns |

## Changelog

- 0.1 (2026-10-30, ADR-0024 amendment): initial: session record, start, list, end one, end
  others, sign-out, check with membership; the assertion's session is checked on every call;
  tenant-store migration 0011 (`console_sessions`, global).
