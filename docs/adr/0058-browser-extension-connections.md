# ADR-0058 — Browser extension: per-person browser connections, recorded with the person and the site

- **Status:** Proposed
- **Date:** 2026-11-10
- **Deciders:** product owner (asked for the extension and chose its scope on 2026-11-10), engineering

## Context

UMAI governs three surfaces under one control plane: the organization's own AI applications,
its agents, and its people's daily AI use in the browser (vision documents, ADR-0039). The
previous UMAI platform had a Chrome extension for the third (its code was read once, at the
product owner's request, as a reference for the new one): content scripts on ChatGPT, Gemini, Claude and Copilot intercept the send
button and Enter, ask the service worker for a decision, show *warn / justify / redact / block*
dialogs and re-submit; a local DLP scanner and policy pack, a hash-chained event queue uploaded to
an ingest endpoint, Shadow-AI navigation blocking, and a *Connect organization* flow in which a
signed-in console page pushes the configuration into the extension
(`chrome.runtime.sendMessage`, `externally_connectable`).

On 2026-11-10 the product owner asked for "an extension like that, the same way of working, but
on the new decision engine, with all activity visible in the new console", and chose: a separate
repository; the console shows **which person** sent each prompt; **prompts only** (answers are not
checked); no Shadow-AI site blocking in the first version.

What the engine had:

- `POST /v1/evaluate` with the actions the old dialogs need: `ALLOW`, `ALLOW_WITH_WARNINGS`,
  `ALLOW_WITH_MODIFICATIONS` (with `redacted_text`), `STEP_UP_APPROVAL`, `BLOCK`.
- Every evaluation already becomes a decision event (ADR-0041) shown on the console's Detections
  page; `destination.app` is stored as sent.
- But: "Credentials are held by server-side callers only (SDKs, the console's backend), never by
  browsers" (evaluation-api); `source` is `api` or `gateway`; records hold no user id ("user and
  session ids are not recorded", ADR-0041 §2); `subject.user_id` is whatever the caller writes;
  open questions Q10 (will the extension send context?) and Q11 (may the extension replace text?).

## Decision

1. **D1 — A separate repository, and the engine decides.** `umai-browser-extension` (TypeScript,
   Manifest V3, esbuild), like the console (ADR-0016): its own stack and its own release channel
   (Chrome Web Store). It keeps the old extension's content-script structure (site adapters,
   submit interception, dialogs) and drops its local DLP, local policy, event queue and ingest:
   the service worker calls `POST /v1/evaluate` and the engine's decision event is the record.
   One detection, one policy, one record for every surface.
2. **D2 — A browser connection is a person's own API key.** `POST /v1/browser/connections`
   (any role, a person only) makes a key with `client: browser`, the person's `user_id`, a device
   label, the scope `evaluate` only, `env: live`, and the organization's browser application
   (D3). At most 10 active per person and organization. The person or an owner/admin revokes it;
   leaving or being removed from the organization revokes all of the person's connections. No
   expiry: an expiry would stop every connected browser's prompts at once, fail-closed (D5).
   This is the one place a key lives in a browser, and it is bounded: an extension's storage is
   not readable by any page; the key can only evaluate (no gateway, no reads); it names one
   person, so every call is attributed; the console lists and revokes it; the per-key rate limit
   and the tenant's monthly quota bound what its holder can spend. Its holder can read it (as
   the person who typed the prompt anyway) — a person misusing their own key spends their
   organization's quota under their own name.
3. **D3 — One application serves the organization's browsers.** `browser.application_id` in the
   tenant's applications file (gateway-api 0.15); its guardrail applies to every connection (one
   guardrail per application, ADR-0039 §2). A connection keeps the application it was made with
   (a key's application is fixed, credentials 0.6); changing the setting affects new connections.
4. **D4 — The record names the person and the site, from the credential.** The engine API sets
   `source: browser` and `user_id` (the tenant store's `users.id`) from the admitted key, never
   from the body (rule 3; a `subject.user_id` stays unrecorded). The extension sends
   `destination: {app: <site host>, class: external_llm}`, already stored. The policy service
   filters by `user_id` and `destination_app` and resolves people (`user_id` → e-mail, name) at
   read time, so the record holds an id and a person's later rename shows. This narrows ADR-0041
   §2 for this one source: the person is a member of the organization, the identity comes from
   their own console sign-in, and the organization chose to see it. Informing employees is the
   organization's duty (KVKK); the console's connect page says what is recorded before a person
   connects. Still never recorded: the prompt, the masked text, values, hashes.
5. **D5 — What the browser does with a decision.** `ALLOW` sends. `ALLOW_WITH_WARNINGS` asks
   (*Send anyway* / *Cancel*). `ALLOW_WITH_MODIFICATIONS` shows the masked text and sends it only
   when the person confirms; the original is never sent (answers **Q11**). `STEP_UP_APPROVAL` and
   `BLOCK` do not send (there is no approval flow yet). Any failure — not connected, revoked key,
   413, 429, 422, 5xx, network error, no answer in 5 s — does not send, with the reason and, where
   it can help, *Try again*. There is no fail-open setting (rule 4).
6. **D6 — The connect handshake.** The console page `/browser-extension/connect` makes the
   connection through its relay, then sends it to the extension with
   `chrome.runtime.sendMessage(<extension id>, UMAI_CONNECT)`. The extension accepts external
   messages only from its manifest's console origins and that path, refuses an `api_url` it has
   no host permission for, and never answers with the key. If the extension does not accept the
   connection, the console revokes it. A stable development extension id comes from a public key
   in the manifest; the console reads the ids it talks to from `UMAI_BROWSER_EXTENSION_IDS`.
7. **D7 — Not in this version.** Checking answers (`POST_LLM`): the product owner chose prompts
   only; it would double evaluations and L1b cost per conversation. Shadow-AI site blocking and
   site visit events (they need an event ingest beside decisions). Files and attachments
   (added by the amendment of 2026-11-11, below). Enrollment of force-installed fleets without a console sign-in (managed configuration with an
   enrollment token, or OIDC inside the extension). A console role for people who only use the
   extension: today a connecting person is a console member and, as a viewer, can read the
   organization's records.

## Amendment 2026-11-11 — attached files, and the live sites

After the deploy to umai-beta the product owner tried the extension on the live sites and asked for
attached files to be checked too. Decisions (product owner, 2026-11-11):

- **A1 — Files are checked when they are attached, not when the prompt is sent.** The sites upload
  a file as soon as it is chosen; a check at send time would come after the upload. The extension
  catches the file input's `input`/`change`, `drop` and `paste` before the site, reads the text in
  the page and gives the site again only the files the engine allowed. The text goes through
  `POST /v1/evaluate` like a prompt, in pieces of 30 000 characters (at most eight), with request
  ids `bf-…` so records show a file (browser-extension 0.2). No engine change: the guardrail and
  the records are the same.
- **A2 — PDF is read with pdf.js** (Mozilla, Apache-2.0), bundled into the extension and loaded
  only when a PDF is attached; DOCX/XLSX/PPTX/ODF are read from their ZIP with the browser's own
  `DecompressionStream`, text-like files directly.
- **A3 — A file the extension cannot read is attached without a check.** Images and screenshots,
  archives, legacy binary Office files, files over 25 MB, damaged or encrypted files: the person is
  told, nothing reaches the engine, nothing is recorded. This is an explicit exception to D5 and to
  rule 4's "recorded", chosen by the product owner for daily use (a screenshot pasted into ChatGPT
  is not stopped). Recording such attachments needs the event ingest of D7.
- **A4 — A file whose data may only be sent masked is not attached.** A file cannot be masked
  reliably (formatting, formulas, embedded parts); the person is told what was found.

The live sites also showed why selector lists need care: on 2026-11-11 chatgpt.com had no
`#prompt-textarea`; its composer was an unnamed `contenteditable` in the form, with a hidden copy,
and the send button only `type="submit"` with a translated label. The extension (0.1.1–0.1.3) now
listens on `window` from `document_start`, prefers visible elements, checks the input the person
typed in (and sends with that input's own button), and covers grok.com.

## Alternatives considered

| Option | Why not |
|---|---|
| One application key in the extension's managed configuration, `subject.user_id` from the browser profile | The same secret on every machine, copyable; the person is whatever the browser says (rule 3); no per-person revocation |
| The extension calls the console's relays (cookie session) | Puts the console on the request path of every prompt; its CSP, Origin check and SameSite cookie are built to refuse exactly such cross-origin calls; the console never calls the engine for a customer's traffic (ADR-0016) |
| OIDC sign-in inside the extension (`launchWebAuthFlow`) and a token exchange | The right shape for fleets (D7), but needs an Auth0 application per deployment, a token endpoint on the engine (ADR-0023 lists OAuth client credentials as later) and refresh handling; the console sign-in already proves the person |
| Keep the old local DLP and policy in the extension, upload events | Two detectors and two policies that drift; the engine's L0 catalog, L1b policies and records would not apply to the browser |
| Store e-mail and name in each record | Personal data copied into every append-only row and every export; a rename or an erasure would not reach them |

## Consequences

- Credentials 0.10, tagged-action 0.8, usage 0.4, gateway-api 0.15, audit 0.3, data-export 0.2,
  organizations 0.7, and the new browser-extension 0.1 contract. Tenant-store migration 0015
  (`api_keys.client`, `user_id`, `device_label`; `decision_events.user_id` with an index).
- The engine accepts `source: browser` in records, webhooks' `by_source` and usage.
- Members who only use the extension see more of the console than they need (D7); until a
  narrower role exists, an organization connects people it already trusts with a viewer seat.
- The extension's console origins and engine origins are fixed at build time; a self-hosted
  install builds the extension with its own origins.
- Selector-based capture breaks when a site changes its page; the extension says so (it cannot
  intercept) rather than letting prompts through unchecked only where it can detect the input.

## Validation

- Engine: `tests/integration/test_browser_connections.py` (routes, permissions, limits, revocation
  on leave/removal, audit), `tests/integration/test_records_api.py` (source, `user_id`,
  `destination_app`, `people`), admission and registry tests for `client`/`user_id`, migration
  0015 on SQLite and PostgreSQL (`tests/db/`).
- Extension: `npm test` (decision mapping per answer, the request it sends, the handshake's sender
  and payload checks, jsdom capture tests); live sites not covered by tests.
- Console: unit tests of the connect payload and filters; the local stack end to end.
