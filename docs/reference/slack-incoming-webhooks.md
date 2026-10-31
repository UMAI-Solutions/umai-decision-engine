# Slack incoming webhooks

**Last verified:** 2026-10-30 · **Used by:** outbound webhooks, kind `slack` (ADR-0048, contract webhooks 0.1) · Labels: see [README](README.md).
**Vendor claims** from docs.slack.dev and slack.com unless marked otherwise. All sources below were
read on 2026-10-30; the pages show no last-updated date.

## Source keys

| Key | Page |
|---|---|
| IW | [Sending messages using incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/) |
| ERR16 | [Changes to errors for incoming webhooks (changelog 2016-05-17)](https://docs.slack.dev/changelog/2016-05-17-changes-to-errors-for-incoming-webhooks) |
| RL | [Rate limits](https://docs.slack.dev/apis/web-api/rate-limits/) |
| PM / FMT | [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/) / [Formatting message text](https://docs.slack.dev/messaging/formatting-message-text/) |
| BK / SEC / BTN | [Block Kit](https://docs.slack.dev/block-kit/), [Section block](https://docs.slack.dev/reference/block-kit/blocks/section-block/), [Button element](https://docs.slack.dev/reference/block-kit/block-elements/button-element/) |
| WT / WF | [Creating webhook triggers](https://docs.slack.dev/tools/deno-slack-sdk/guides/creating-webhook-triggers/) / [Help: workflows using webhooks](https://slack.com/help/articles/360041352714-Create-more-advanced-workflows-using-webhooks) |
| GH869 | [slackapi/node-slack-sdk#869](https://github.com/slackapi/node-slack-sdk/issues/869) (**secondary source**; Slack maintainers comment) |

## URL and hosts

- Shape `https://hooks.slack.com/services/T…/B…/…`, "specific to a single user and a single channel"
  (IW). IW's `oauth.v2.access` example omits `/services/`; likely a doc typo (**unverified**).
- GovSlack: IW says GovSlack apps call `slack-gov.com` instead of `slack.com`; the webhook host
  `hooks.slack-gov.com` appears only in third-party code (releases.sh, Ansible): **unverified**.
- Workflow Builder webhooks are a different integration: URLs start `https://hooks.slack.com/triggers/`
  (WF, WT; older guides show `/workflows/`, **secondary source**); the body is a flat JSON object of
  variables defined in the workflow (nested → `parameter_validation_failed`, a missing variable fails
  the workflow); success is `{"ok":true}`, not `ok`; 1 request/s (WT, WF).

## Request

- `POST`, `Content-type: application/json`; `text`, `blocks`, legacy `attachments` (IW). Channel,
  username and icon "cannot be overridden"; they come from the app and the installer's choice (IW).
- `text`: IW lists error `no_text` (text missing). PM: not enforced with `blocks` but "highly
  recommended" as notification fallback; screen readers and mobile notifications use only `text`
  (PM, FMT). Whether a webhook rejects a blocks-only body: **unverified**. Always send `text`.
- mrkdwn is the default for `text`; `"mrkdwn": false` turns it off; blocks use text type `mrkdwn` or
  `plain_text`. Link `<https://example.com|label>`; escape only `&`, `<`, `>` as `&amp;`, `&lt;`, `&gt;` (FMT).
- Limits: `text` ≤ 4 000 chars recommended, truncated above 40 000 (PM; Web API, assumed for
  webhooks); ≤ 50 blocks per message (BK); section `text` 1–3 000 chars, ≤ 10 `fields` of ≤ 2 000
  chars (SEC); more than 100 attachments → `too_many_attachments` (IW).

## Responses

- Success: HTTP 200, plain-text body `ok`; any other status is a failure (IW, ERR16).
- With status (ERR16): 400 `invalid_payload`, 400 `user_not_found`, 403 `action_prohibited`,
  404 `channel_not_found`, 410 `channel_is_archived`, 500 `rollup_error`.
- Without status (IW): `invalid_token`, `no_active_hooks` (disabled), `no_service` (disabled, removed
  or invalid), `no_service_id`, `no_team`, `team_disabled`, `no_text`, `posting_to_general_channel_denied`,
  `too_many_attachments`. 404 `no_service` for a revoked URL: **secondary source**; others **unverified**.
- Classification (ours, from IW's descriptions):
  - **Endpoint dead** (disable): `no_service`, `no_active_hooks`, `no_service_id`, `no_team`,
    `team_disabled`, `invalid_token`, `channel_not_found`, `channel_is_archived`, `user_not_found`.
  - **Admin change needed** (stop): `action_prohibited` (IW: don't retry for now), `posting_to_general_channel_denied`.
  - **Our payload** (don't resend as is): `invalid_payload`, `no_text`, `too_many_attachments`.
  - **Transient** (retry with backoff): 429, 5xx (`rollup_error`), timeouts, connection errors.

## Rate limits and security

- 1 per second per webhook, "short bursts >1 allowed"; no more than one message per second per
  channel, sustained excess is rate limited; over the limit: HTTP 429 with `Retry-After` in seconds (RL).
- No signature, header or verification is documented: the URL is the credential. "Your webhook URL
  contains a secret"; don't share or commit it; "Slack actively searches out and revokes leaked
  secrets" (IW).

## Buttons vs links

- A button with `url` opens the link, but the app "will still receive an interaction payload and will
  need to send an acknowledgement response" (BTN). With no interactivity Request URL answering 200,
  desktop clients show a warning icon after the click; maintainers call it intended (GH869,
  **secondary source**). A mrkdwn link `<url|label>` has no round trip: prefer links.

## What UMAI relies on

- Accept only `https://hooks.slack.com/services/…` (exact host, HTTPS, no redirects followed);
  GovSlack and Workflow `/triggers/` URLs are rejected until verified and designed separately.
- The URL is a secret: stored encrypted (AES-GCM, key derived from the webhook master key), never
  logged, shown masked (scheme, host, last four characters) in the console.
- Payload: top-level `text` summary plus a few mrkdwn section blocks; ids, counts and console links as
  `<url|label>` (no buttons); interpolated values escaped for `& < >`; never content.
- Only the status code is used (the body is never read): 2xx is success; 403, 404 and 410 turn the
  endpoint off (`gone`); anything else is retried on the webhook schedule (first retry after one
  minute, longer than Slack's `Retry-After`).
- Aggregate and send at most one message per second per endpoint; never rely on the burst allowance.
