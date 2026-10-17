# ADR-0015 — Policy changes are gated by golden tests that run through the real pipeline

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)

## Context

Policies are data (ADR-0012) and will be edited by people who are not engine developers:
the security / compliance team now, customers through the dashboard later (ADR-0013). After
the L0 gate (ADR-0014) the behaviour of a bundle depends on `on_detect`, exceptions and
thresholds working together; a one-line change to an exception can allow national IDs to an
external LLM. Until now the only check was `cli validate` (schema and semantics) plus manual
requests from `dev/requests.http`. Nothing stated what a bundle is *supposed* to decide, so
nothing could fail when it stopped doing so.

The policy lifecycle design already names a "test" stage with golden cases in the bundle's
`tests/` directory, but its format, runner and CI gate did not exist.

## Decision

1. Each bundle version may carry golden cases in `policies/<id>/<version>/tests/*.yaml`
   (contract [policy-tests](../contracts/policy-tests.md) 0.1): input + context → expected
   decision fields.
2. Cases are **YAML**, not JSONL: they are written and reviewed by people, need comments,
   Turkish text and multi-line context, and diff well in PRs.
3. `tests/` is **not part of the signed artifact** and is never read by the engine at runtime.
   Regression cases can be added to a published version without re-signing it.
4. The runner (`python -m umai_decision_engine.policy.cli test <path>`) loads bundle versions
   through the regular `BundleStore` and evaluates every case through the regular `Evaluator`
   — no separate decision logic for tests. Decisions are checked, not latency (the runner uses
   a generous timeout).
5. CI runs, on every push and PR: ruff, pytest, and `cli test policies --require-tests`. A
   failing case, an invalid bundle or test file, a broken active pointer, or an active
   version without golden tests fails the build.
6. Runner output contains case ids and decision fields only, never case input text.

## Alternatives considered

| Option | Why not |
|---|---|
| JSONL cases, as first sketched in the bundle contract | Hard to write and review by hand (no comments, one-line Turkish strings, escaped quotes); the eval datasets stay JSONL because they are generated |
| Policy tests as pytest files next to the code | Policy authors would have to write Python; the dashboard could not create or run them; tests would not travel with the bundle version |
| Golden cases inside `bundle.yaml` | Changing a test would change the signed bundle hash and require re-publishing |
| A separate evaluation path for tests (call the L0 gate directly) | Could pass while the real pipeline fails (bundle resolution, phase checks, MONITOR mode, failure policy) |
| Only the `l1-decisions` / `l0-pii-tr` evals | Evals measure accuracy over datasets; they do not state "this bundle must block this" and are not a pass/fail gate for one policy change |

## Consequences

- A policy change is reviewed together with the cases that show its effect; a reviewer reads
  expectations, not only YAML diffs.
- Golden cases exercise L0 detection too: a recognizer change that alters a policy outcome
  fails CI. Detection quality itself is still measured by the `l0-pii-tr` eval (F0).
- When L1b exists, model-backed cases use `acceptable` (a set of actions) and run against the
  `fake` provider in CI; accuracy of the real model is the `l1-decisions` eval's job.
- The dashboard (F5) reuses the runner for its "test" stage; its playground evaluates a draft
  inline bundle with the same request builder.
- The decision response does not yet list which exception or rule decided; cases use
  `reason_contains`. A structured field (e.g. matched rule ids) is expected with the L1a rule
  engine and would be a contract change of the evaluation API.

## Validation

- `pytest` covers the runner: passing and failing cases, every comparison, load-time errors,
  duplicate ids, invalid bundles, manifest mismatch, path selection, `--require-tests`, and
  that output never contains case input.
- The demo bundle's golden cases pass in CI on Linux and Windows.
- Revisit if policy authors find the YAML format a barrier (then the dashboard generates it)
  or if case counts make the run slow (then run cases concurrently).
