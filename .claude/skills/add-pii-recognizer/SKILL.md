---
name: add-pii-recognizer
description: Add or change an L0 entity recognizer (Turkish ID, IBAN, phone, secret pattern, tenant custom entity) with validation, context words, tests, eval cases and docs. Use whenever a new PII/secret type must be detected or an existing recognizer's precision/recall must change.
---

# Add a PII recognizer (L0)

Read first: `docs/architecture/l0-entity-detection.md`, `docs/reference/presidio.md`,
`docs/reference/turkish-identifiers.md`.

## Steps

1. **Specify the entity.** Name it `UPPER_SNAKE` (`TR_` prefix for Turkish-specific types).
   Write down: pattern (with separator variants), validator (checksum or none), Turkish and
   English context words, default on/off, expected false-positive sources.
2. **Validator first.** Add a pure function to `src/umai_decision_engine/l0/validators.py`.
   Unit-test it with valid vectors, invalid-checksum vectors, separator variants and edge
   cases. Take vectors from `docs/reference/turkish-identifiers.md`; if the type is new,
   add its algorithm and synthetic vectors there first, computed and checked by code,
   never typed by hand.
3. **Recognizer.** Implement in `l0/recognizers/<family>.py` as a Presidio
   `PatternRecognizer` subclass using the validator (check `docs/reference/presidio.md`
   for the current hook names; do not guess them). Include context words in both languages.
4. **Register** it in the analyzer factory and add the type to the bundle schema's allowed
   entity list.
5. **Contracts.** Add `data.pii.<type_lower>` (or `data.secret.*`) to
   `docs/contracts/tag-taxonomy.md` with a minor version bump and changelog line; update the
   entity catalogue table in the L0 doc.
6. **Tests.** Recognizer tests with Turkish and English sentences, separator variants,
   context present/absent, and a false-positive set. Extend the leak test to the new type.
7. **Eval.** Add synthetic cases (positives and near-miss negatives) to the `l0-pii-tr`
   dataset generator; run `python -m evals.run --suite l0-pii-tr` and `--suite l0-latency`;
   compare with the last summary.
8. **Done when** tests and ruff pass, precision did not drop for other types, L0 p95 is
   within budget, and docs are updated.

Never use real identifiers anywhere, including comments.
