---
name: change-contract
description: Change any versioned data shape (evaluation request/response, policy bundle, entity detection result, decision questions, tagged action / decision event, L2 analysis, tag taxonomy). Use before adding, renaming or removing a field, enum value or tag.
---

# Change a contract

Read first: `docs/contracts/README.md` (versioning rules) and the contract document.

1. Decide minor vs major (README rules). During `0.x`, bump the minor either way and write
   "breaking" in the changelog line if it is.
2. Edit the contract document: field table, example payload, changelog line with today's date.
3. Edit the Pydantic model in `src/umai_decision_engine/contracts/` to match.
4. Doc examples are tests: make sure the example JSON/YAML in the document parses with the
   model (`tests/unit/test_contract_examples.py`).
5. Update producers and consumers in the same change; search for the field name across
   `src/`, `tests/`, `evals/`, `policies/`.
6. If the change affects the cutover mapping, update
   `docs/platform-context/compatibility-contract.md`.
7. If the change is a decision between alternatives, add an ADR (skill `write-adr`).
8. Never add a field that carries raw content outside L0 (ADR-0005).
