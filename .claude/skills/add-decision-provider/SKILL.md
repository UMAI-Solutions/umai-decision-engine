---
name: add-decision-provider
description: Implement or update an L1b decision-model provider adapter (TypeSafe Jev, OpenAI Decisions API, gpt-oss-safeguard prompted mode, or a new vendor) behind the DecisionModel interface, with capability declaration, error mapping, tests and reference docs.
---

# Add or update a decision-model provider (L1b)

Read first: `docs/architecture/l1-policy-decision.md`, `docs/contracts/decision-questions.md`,
`docs/adr/0004-decision-model-provider-abstraction.md`, and the provider's file in
`docs/reference/`.

## Rules

- The provider's API is newer than your training data. Use only what
  `docs/reference/<provider>.md` documents and re-verify it on the vendor's site first
  (skill `verify-vendor-facts`). If a field or endpoint is undocumented, stop and record
  it in `docs/open-questions.md` instead of guessing.
- Adapters translate; they never change the provider-agnostic contract.
- No request or response bodies in logs. No user identifiers in payloads (role,
  department and destination class only). Input is `redacted_excerpt` + facts.

## Steps

1. **Capabilities.** Declare `ProviderCapabilities`: probabilities, question types
   (`choice`, `condition`, `score`), max questions per call, max input size, image input.
2. **Compile.** Map `DecisionRequest` to the provider request. Document the mapping in a
   table at the top of the module and in the reference doc (e.g. `condition` → Jev Noul;
   → OpenAI two-option choice).
3. **Call.** Use the shared async httpx client with an explicit timeout from the caller.
   No retries inside the call (the pipeline decides on fallback).
4. **Parse.** Build `DecisionResult`. Answers outside declared options →
   `DecisionModelError(kind="invalid_response")`. Missing probabilities → `None`, never invented.
5. **Errors.** Map HTTP/transport failures to `timeout`, `rate_limited`, `unavailable`,
   `invalid_response`, `auth`.
6. **Settings.** Add base URL, model id and API key names to settings and `.env.example`
   (names only).
7. **Tests.** Unit tests with mocked HTTP (httpx `MockTransport` or `respx`): mapping
   snapshot, each error kind, out-of-options answer, missing probabilities. A live test in
   `tests/live/` marked `@pytest.mark.live`.
8. **Eval.** Run `python -m evals.run --suite l1-providers --provider <name>` and
   `--suite l1-calibration`; record region and model id in the summary.
9. **Docs.** Update the reference doc's mapping section and `Last verified` date, and
   ADR-0004 if the default changes.
