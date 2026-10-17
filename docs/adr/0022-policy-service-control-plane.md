# ADR-0022 — Policy service: drafts, tests and publishing from the console

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)
- **Answers:** open question Q17 (where the policy control plane lives)

## Context

The product owner wants to edit guardrails in the console — switch policies on and off, change
their settings — and later create policies there (ADR-0013 direction). The engine only reads
signed, immutable bundles (ADR-0012), and guardrails pin policy versions (ADR-0021). Golden
cases (ADR-0015) must keep passing for every active version. The product owner chose
(2026-10-17): the service lives **in this repository as a separate service**; a policy is
**on or off**; **Publish runs the tests and activation is optional** ("Publish & activate" or
"Publish only").

## Decision

1. **Policy service** (`umai_decision_engine.control_plane`, its own process, default port 9200):
   drafts (SQLite, one per guardrail), validation with the engine's schema and
   `validate_bundle`, golden tests through the engine's runner, publish, activate. It is the only
   writer of the bundle source; the engine stays read-only and picks changes up by polling (or
   an explicit reload).
2. **Draft → publish.** A draft starts from a version (default: the active one); older bundles
   are converted to policy form. Drafts save even when invalid (no lost work) and list their
   problems; publishing requires a valid draft and passing golden cases. The new version is
   `max + 1`, written atomically with a manifest (signed when `UMAI_DE_SIGNING_KEY_FILE` /
   `_ID` are set), the base version's `tests/` copied along, and an entry in
   `<bundle>/changelog.jsonl`. Activation (also rollback) replaces the `active` pointer
   atomically and is logged.
3. **On / off.** `l0.policies[].enabled` (policy-bundle 0.7): an off policy stays in the guardrail
   with its configuration and is not evaluated.
4. **Accepting changed behaviour.** When a change alters a golden case's outcome on purpose, the
   reviewer accepts it; at publish the case's expectation is rewritten from the observed decision
   (same fields the author asserted). Unaccepted failures block the publish. CI therefore stays
   green and every behaviour change is an explicit review step.
5. **Console** calls the service from its server side (`UMAI_CONTROL_URL`, `UMAI_CONTROL_TOKEN`)
   and asks the engine to reload after publish / activate. The service requires a bearer token
   outside development.

## Alternatives considered

| Option | Why not |
|---|---|
| Own repository for the service | Would copy or package the engine's validation and test code; the product owner chose this repository |
| Console (Next.js) writes bundles | Validation and golden tests are Python; duplicating them or calling the engine to write would break ADR-0012 and does not fit self-hosted |
| Engine endpoint that writes bundles | The engine would stop being a pure reader of signed artifacts (ADR-0012) |
| Three states (on / monitor / off) | The product owner chose two states; per-policy monitor can be added later |
| Publish = activate always | Less control; the product owner chose optional activation |
| Block the publish on any failing golden case, no acceptance | Every intended change (switching a policy off) would need hand-edited YAML |

## Consequences

- Editing happens in the console; the repository's `policies/` is the bundle source in R&D, so a
  publish from a console pointed at it creates new version directories there (commit or discard
  them like any change). Production deployments point both services at their own store.
- Not built yet: policy library shared across guardrails, editing exceptions and custom
  detectors in the console, four-eyes approval, per-user identity in the change log (the console
  has no login yet), `http_pull` for remote engines.

## Validation

`tests/integration/test_policy_service.py`: legacy conversion, invalid drafts, switching a policy
off with accepted cases, publish only, rollback, signing, auth. Verified end to end in the
console on 2026-10-17: draft → test (37/52) → accept 15 → publish & activate v5 → engine served
v5 → rollback to v4.
