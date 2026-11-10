# Contract: browser extension

**Version:** 0.1 · **Producer → consumer:** console ↔ policy service (browser settings,
browser connections), console page → extension (connect handshake), extension → engine
(`POST /v1/evaluate`) · **Decisions:** [ADR-0058](../adr/0058-browser-extension-connections.md)

The UMAI browser extension (repository `umai-browser-extension`, Chrome Manifest V3) checks
what a person types into ChatGPT, Gemini, Claude and Microsoft 365 Copilot before it is sent.
It asks the engine (`POST /v1/evaluate`, [evaluation-api](evaluation-api.md)) and acts on the
decision; there is no policy and no detection in the extension. Every check is an ordinary
decision event ([tagged-action](tagged-action.md) 0.8, `source: browser`) with the person and
the site, so the console's Detections page shows the browser's activity.

A browser is connected by the person themself, signed in to the console: the policy service
makes a **browser connection**, an API key that belongs to that person and to the organization's
browser application ([credentials](credentials.md) 0.10), and the console hands it to the
extension.

## Browser settings

Permissions (ADR-0029 matrix): `browser.read` — every role, and the operator (the control token
with `X-Umai-Tenant`). `browser.connect` is a person's own act — a member of the organization with
any role; the control token alone is refused (403 `actor_required`), a person who is not a member
gets 403 `forbidden`. `browser.manage`: owner, admin and the operator.

One application serves every browser of an organization (ADR-0058 D3). It is stored in the
tenant's applications file ([gateway-api](gateway-api.md) 0.15) as

```yaml
browser:
  application_id: browser   # null or absent: browsers are not set up
```

The application must exist in the same file; deleting it while it is the browser application is
refused like any other inconsistent file (422 `invalid_configuration`, naming the browser
extension).

| Route | Permission | Does |
|---|---|---|
| `GET /v1/browser/settings` | `browser.read` | The settings and the application they name |
| `PUT /v1/browser/settings` with `{"application_id": "<id>" \| null}` | `browser.manage` | Replaces them; 422 `invalid_configuration` for an unknown application or another field. Audit action `browser.settings.update` |

```json
{
  "tenant_id": "smarttech",
  "browser": { "application_id": "browser" },
  "application": { "application_id": "browser", "name": "Browser extension", "guardrail": "gr-demo-data-protection", "enabled": true }
}
```

`application` is `null` when no application is set. Changing the application does not move
existing connections: a key's application is fixed when it is made (credentials 0.6), so a person
reconnects to move.

## Browser connections

A browser connection is an API key with `client: "browser"`, the person's `user_id`, a
`device_label`, the scope `evaluate` only, `env: live`, and the browser application. Only these
routes make one; `POST /v1/tenants/{id}/keys` cannot (it has no `client` field). The key routes
still list and revoke them.

| Route | Permission | Does |
|---|---|---|
| `POST /v1/browser/connections` with `{"device_label": "Chrome on Windows"}` | `browser.connect` (a person) | Makes a connection for the person who asks. 201 with the secret, shown once |
| `GET /v1/browser/connections?scope=mine` (default) | `browser.connect` | The person's own connections, newest first |
| `GET /v1/browser/connections?scope=all` | `browser.manage` | Every connection of the organization, newest first |
| `DELETE /v1/browser/connections/{key_id}` | `browser.connect` for one's own; `browser.manage` for anyone's | Revokes it (idempotent). 404 for an unknown key, a key that is not a browser connection, or — without `browser.manage` — another person's |

`device_label`: 1–120 characters, optional (default `Browser`); the console derives it from
the browser's user agent (`Chrome on Windows`). The key's `name` is `Browser · <device_label>`.

```json
{
  "connection": {
    "key_id": "Q7h2kP9c",
    "tenant_id": "smarttech",
    "user": { "user_id": "0f9d2c1e-6a4b-4d8e-9a51-2b7c3e1f0a99", "email": "ada@example.com", "name": "Ada Example" },
    "device_label": "Chrome on Windows",
    "application_id": "browser",
    "created_at": "2026-11-10T09:00:00Z",
    "revoked_at": null,
    "status": "active"
  },
  "secret": "umai_live_eu_Q7h2kP9c_<43 base62 characters>",
  "published": true
}
```

The list answers `{"tenant_id", "connections": [<connection>, ...]}`; `DELETE` answers
`{"connection": <connection>, "published": <bool>}` (`published` is `false` when the connection
was already revoked). `user` carries the person as the tenant store knows them now; `email` and
`name` are `null` for a person no longer in the store.

| Refusal | When |
|---|---|
| 403 `actor_required` | Any `browser.connect` route without a person (the control token alone): a connection names a person |
| 403 `forbidden` | `scope=all` or another person's connection without `browser.manage` |
| 409 `browser_not_set_up` | `POST` while `browser.application_id` is unset, or names an application that is missing or disabled |
| 409 `too_many_connections` | `POST` when the person already has 10 active connections in this organization |
| 422 `invalid_request` | `device_label` empty or longer than 120 characters, or another field |

A person who leaves an organization or is removed from it loses every connection there: they
are revoked in the same change ([organizations](organizations.md) 0.7). Connections do not
expire (ADR-0058 D2). Audit actions: `browser.connect` (target the key id), `browser.disconnect`.

## Connect handshake (console page → extension)

The console page `/browser-extension/connect` (signed in, any role) talks to the extension with
`chrome.runtime.sendMessage(<extension id>, <message>, <callback>)`. The extension accepts
external messages only from the console origins in its manifest's `externally_connectable`
(`http://localhost:3100`, the umai-beta and umai-prod hosts, more at build time) and only from
pages whose path starts with `/browser-extension/connect`; anything else gets
`{"ok": false, "error": "untrusted_sender"}`. The development extension id is
`kmmpljdkmpjnafljicdlgnflemoboieo` (from the `key` in the extension's manifest); the console
reads the ids it talks to from `UMAI_BROWSER_EXTENSION_IDS` (comma-separated, that id by
default).

| Message | Answer |
|---|---|
| `{"type": "UMAI_PING"}` | `{"ok": true, "version": "<extension version>", "state": <state>}` |
| `{"type": "UMAI_CONNECT", "payload": <connection payload>}` | `{"ok": true, "state": <state>}`, or `{"ok": false, "error": "invalid_payload" \| "host_not_permitted", "detail": "<sentence>"}` |
| `{"type": "UMAI_DISCONNECT", "payload": {"key_id": "Q7h2kP9c"}}` | `{"ok": true, "state": <state>}`; clears the extension only when its connection has that key id |

Connection payload (`version` 1):

```json
{
  "version": 1,
  "api_url": "https://umai-beta.westeurope.cloudapp.azure.com",
  "key": "umai_live_eu_Q7h2kP9c_<43 base62 characters>",
  "key_id": "Q7h2kP9c",
  "organization": { "id": "smarttech", "name": "Smarttech" },
  "user": { "email": "ada@example.com", "name": "Ada Example" },
  "console_url": "https://umai-beta.westeurope.cloudapp.azure.com"
}
```

`api_url` is where the engine's `/v1/evaluate` is reached (the console's `UMAI_PUBLIC_API_URL`);
the extension refuses an `http:` URL other than `localhost`/`127.0.0.1`, and an origin its
manifest has no host permission for (`host_not_permitted`). State, never with the key:

```json
{ "connected": true, "organization": { "id": "smarttech", "name": "Smarttech" }, "user": { "email": "ada@example.com", "name": "Ada Example" }, "key_id": "Q7h2kP9c", "api_url": "https://umai-beta.westeurope.cloudapp.azure.com", "connected_at": "2026-11-10T09:00:01Z" }
```

`{"connected": false}` when not connected. The console makes the connection first and sends
`UMAI_CONNECT` second; when the extension does not answer `ok`, the console revokes the
connection it just made. Revoking the current browser's connection on the console sends
`UMAI_DISCONNECT`.

## Evaluation from the extension

For each prompt the person sends on a covered site, the extension's service worker calls
`POST <api_url>/v1/evaluate` with `Authorization: Bearer <key>`:

```json
{
  "contract_version": "0.14",
  "request_id": "br-6f1c2d3e4a5b46c78d9e0f1a2b3c4d5e",
  "timestamp": "2026-11-10T09:01:00Z",
  "phase": "PRE_LLM",
  "content": {
    "messages": [{ "role": "user", "content": "<the prompt as typed>" }],
    "focus": "LAST_USER_MESSAGE",
    "content_type": "text"
  },
  "destination": { "app": "chatgpt.com", "class": "external_llm" },
  "timeout_ms": 3000
}
```

`request_id` is `br-` and 32 hex characters (a UUID without dashes). `destination.app` is the
site's host without `www.` (`chatgpt.com`, `chat.openai.com`, `gemini.google.com`, `claude.ai`,
`m365.cloud.microsoft`, `copilot.cloud.microsoft`). No `bundle` (the key's application names the
guardrail), no `subject` (the person comes from the key), no `tenant_id`. The engine records the
call with `source: browser`, the key's `user_id` and the destination (tagged-action 0.8).

What the extension does with the answer (ADR-0058 D5):

| Answer | The prompt |
|---|---|
| `ALLOW` | Sent as typed |
| `ALLOW_WITH_WARNINGS` | The person sees `decision.reason` and chooses *Send anyway* or *Cancel* |
| `ALLOW_WITH_MODIFICATIONS` | The person sees `output_modifications.redacted_text` (placeholders such as `<TR_TCKN_1>`) and chooses *Send masked text* or *Cancel*; the original is never sent. Without `redacted_text` it is treated as `BLOCK` |
| `STEP_UP_APPROVAL`, `BLOCK` | Not sent; the person sees `decision.reason` and the entity types found |
| 401, 403 | Not sent: *This browser's UMAI connection was revoked or is not valid; connect it again* |
| 413 `input_too_large` | Not sent: the prompt is longer than the organization allows (`limit` characters) |
| 429 | Not sent: rate limit or monthly quota (`Retry-After` shown when present) |
| 422, 5xx, network error, no answer within 5 s | Not sent: *UMAI could not check this prompt*, with *Try again* |
| Not connected | Not sent: *Connect this browser to your organization* (opens the popup's connect flow) |

The extension never sends a prompt the engine did not allow: there is no fail-open setting.
Prompts that are empty after trimming are not checked and not intercepted. The extension keeps
no prompt, answer or decision on disk; its local storage holds the connection only.

## Changelog

- 0.1 (2026-11-10, ADR-0058): browser settings, browser connections, the connect handshake and
  the evaluation request of the browser extension.
