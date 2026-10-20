# ADR-0014 — An L0 detection is terminal: L0 decides, L1 does not run

- **Status:** Accepted; §1 and §5 amended by ADR-0036 (only `BLOCK` and `STEP_UP_APPROVAL` are terminal; other L0 outcomes are a floor for L1)
- **Date:** 2026-10-16
- **Deciders:** product owner (UMAI)
- **Supersedes:** ADR-0011 (L0 detects, it does not decide)

## Context

With ADR-0011 an input containing a TCKN, IBAN and card number sent to an external LLM was
answered `ALLOW` whenever no L1 rule happened to match — and every such input would also be
sent to the L1b decision model, spending tokens/compute on a case that is already decided.
The product owner's rule: **if L0 caught it, the answer is never a plain ALLOW, and the input
does not go to L1.**

## Decision

1. When L0 detects at least one entity, the **L0 gate** decides and the evaluation ends.
   L1a and L1b do not run; no decision-model call is made.
2. The action is configured per entity type in the bundle (`l0.on_detect`), default `BLOCK`.
   Allowed actions: `BLOCK`, `STEP_UP_APPROVAL`, `ALLOW_WITH_MODIFICATIONS` (the caller gets
   the text with those entity types masked). `ALLOW` and `ALLOW_WITH_WARNINGS` are rejected
   at bundle load: detected PII never passes unmasked by default.
3. **Deterministic exceptions** (`l0.exceptions`) may relax the action for listed entity types
   when their conditions hold (subject role/department, destination class, phase, agent action,
   entity counts). They are rule evaluations — no model call, ~0 ms, 0 tokens. First matching
   exception per type wins; exceptions may set `ALLOW`, `ALLOW_WITH_WARNINGS` or
   `ALLOW_WITH_MODIFICATIONS`.
4. The final action is the strictest across detected types. `STEP_UP_APPROVAL` behaves as
   `BLOCK` in phases without a user interface. `MONITOR` mode still returns `ALLOW` with
   `would_have`.
5. When L0 detects nothing, the evaluation continues to L1 as before.
6. The response records `decided_by: "L0"` and, if used, the exception ids.

## Alternatives considered

| Option | Why not |
|---|---|
| ADR-0011: L0 only detects, L1 decides | Spends L1b tokens on decided cases; a missing L1 rule silently allows PII |
| Every detection → BLOCK, no configuration | Too blunt (e-mail addresses in normal work); product owner chose per-type actions |
| Exceptions via the decision model | Costs tokens on exactly the traffic this ADR wants to keep cheap |

## Consequences

- L0's latency budget now covers the whole runtime path for PII-bearing input.
- L1a and L1b only see input without detected entities. L1b questions about "customer data"
  therefore target what L0 cannot see (paraphrased data, intent, injection).
- The condition evaluator is shared by the L0 gate and L1a (`rules/`).
- Impact preview (ADR-0013) works for `on_detect`/`exceptions` changes: the gate only uses
  entity types/counts and request facts, which decision events keep. Recognizer changes still
  need synthetic cases.

## Validation

Tests: per-type actions, strictest-wins, masking only the configured types, exceptions by
department/destination, STEP_UP in tool phases, no L1 call when L0 detects, bundle validation
rejects `ALLOW` in `on_detect`.
