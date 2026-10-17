# Changing a policy

**In the console (ADR-0022):** open the guardrail, *Edit*, change policies (on / off, action,
sensitivity, roles, detectors) or add one, *Test*, accept intended behaviour changes, then
*Publish & activate* or *Publish only*; roll back from *Versions*. The steps below are the same
flow done by hand in Git, still useful for reviews and CI.

How the team writes, tests and ships policy bundles until the policy dashboard exists (F5).
Until then **Git is the control plane**: a policy change is a pull request, CI is the test
stage, review is the approval, and moving the `active` pointer is the release. The dashboard
will run the same steps with the same code ([ADR-0013](../adr/0013-policy-lifecycle-dashboard.md)).

## What is tested where

| | Detector quality | Policy behaviour |
|---|---|---|
| What changes | Recognizer code in `src/umai_decision_engine/l0/` | `policies/<id>/<version>/bundle.yaml`: `on_detect`, `exceptions`, `min_score`, later L1 rules |
| Who | Engineers | Policy authors (security / compliance), engineers |
| Test | Unit tests + `l0-pii-tr` eval: precision / recall over hundreds of synthetic sentences | **Golden cases** in the version's `tests/`: "this input in this context → this decision", pass / fail |
| Gate | `pytest`, eval report in the PR | `cli test` in CI ([ADR-0015](../adr/0015-policy-golden-tests.md)) |

Golden cases run through the real pipeline, so they also catch a recognizer change that
alters a policy outcome.

## Steps

1. **Branch:** `policy/<bundle-id>-<change>`, e.g. `policy/gr-demo-allow-iban-for-finance`.
2. **New version.** Published versions are immutable (ADR-0012): copy the current version,
   including its `tests/`, and bump the number.

   ```powershell
   Copy-Item -Recurse policies\gr-demo-data-protection\2 policies\gr-demo-data-protection\3
   Remove-Item policies\gr-demo-data-protection\3\manifest.json -ErrorAction SilentlyContinue
   ```

   Set `version: 3` in the new `bundle.yaml`. Never edit the `bundle.yaml` of a version that
   is already published: engines keep their loaded copy and report the modification as an
   error.
3. **Edit** the new `bundle.yaml`.
4. **Write the cases** in `3/tests/` (format: [policy-tests contract](../contracts/policy-tests.md)).
   For every behaviour you add or change, write one case that shows it and one that shows
   its boundary. A new exception gets a case where it applies and one case per condition
   where it does not.
5. **Run locally:**

   ```powershell
   python -m umai_decision_engine.policy.cli test policies\gr-demo-data-protection\3   # fast loop
   python -m umai_decision_engine.policy.cli test policies --require-tests             # what CI runs
   ```

   To try free-form input, start the engine and send requests from
   [`dev/requests.http`](../../dev/requests.http) with `"bundle": {"id": "...", "version": 3}`:
   an explicit version is evaluated before it becomes active.
6. **Pull request.** CI runs lint, tests and every bundle's golden cases on Linux and Windows.
   The reviewer reads the bundle diff and the cases:

   ```powershell
   git diff --no-index policies\gr-demo-data-protection\2\bundle.yaml policies\gr-demo-data-protection\3\bundle.yaml
   ```

7. **Sign** when the target environment requires signed bundles
   (`python -m umai_decision_engine.policy.cli sign ...`; keys never in the repository).
8. **Activate** in a separate, small change: write `3` into `policies/<id>/active`. Engines
   pick it up within the poll interval (default 5 s) or on `POST /v1/bundles/reload`;
   `GET /v1/bundles` shows the active version and any load error. If the new version fails to
   load, engines keep the previous one (last-known-good).
9. **Roll back** by writing the previous number into `active`.

## When something is missed in the field

1. Never paste the real text anywhere (issue, PR, fixture, prompt). Reproduce it with a
   synthetic value of the same shape (vectors in
   [`turkish-identifiers.md`](../reference/turkish-identifiers.md)) and keep the surrounding
   words, because the words are usually what matters ("kimlik nom", "numaram").
2. Add the case to `tests/regressions.yaml` of the active version, with the date in its
   description. `tests/` is not signed, so this needs no new version.
3. If the case fails, decide where the fix belongs:
   - **policy** (threshold, `on_detect`, an exception): new bundle version, steps above;
   - **detector** (the entity is not recognised at all): recognizer change by an engineer
     (skill `add-pii-recognizer`), plus a case in the `l0-pii-tr` eval set.

The case stays forever, so the same miss cannot return unnoticed.

## Writing good cases

- One behaviour per case; the id states the outcome: `kyc-to-external-llm-blocked`.
- Assert `entities` as well as `action`: it catches false positives and wrong entity types.
- For masking, assert `redacted_text`: it shows exactly what the caller receives.
- Use `defaults` for the context most cases share (destination, phase).
- Synthetic data only. The runner never prints input text, but the files are in Git.

## How often

| When | What |
|---|---|
| Every push and PR | CI: lint, `pytest`, `cli test policies --require-tests` |
| Every recognizer change | `l0-pii-tr` eval report in the PR (once the eval harness exists, F0) |
| Before activating a version in production | Impact preview over recent decisions (replay job, after F3) |
